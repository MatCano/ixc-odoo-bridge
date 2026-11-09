"""Sentido Odoo -> IXC (chamado pelo módulo maisfibra_crm ao mudar de estágio).

- Recolher Documentos: cria/atualiza o cliente no IXC como PROSPECT
- Negociação: cria a negociação no IXC (plano + descrição + ID do lead)
- Lead externo (OPA/Chatwoot/landing page): clona no IXC (contato) e no Odoo
"""
import logging
from datetime import datetime

import config
import ixc
from ixc_para_odoo import digitos, funil_do_lead, upsert_lead, upsert_partner

logger = logging.getLogger("IXC-Odoo-Bridge.fluxo")

# Status da negociação no IXC (validado 09/11): N = em andamento, V = ganha (vencemos), D = perdida
STATUS_ABERTA = "N"


def _hoje():
    return datetime.now(config.TZ_BRASIL).strftime("%Y-%m-%d")


def _obs(d):
    partes = [
        f"Origem: {d.get('origem')}" if d.get("origem") else "",
        f"Canal: {d.get('canal_venda')}" if d.get("canal_venda") else "",
        f"Indicado por: {d.get('indicado_por')}" if d.get("indicado_por") else "",
        f"Concorrente: {d.get('concorrente')}" if d.get("concorrente") else "",
        f"Vendedor (Odoo): {d.get('vendedor')}" if d.get("vendedor") else "",
        f"Lead Odoo #{d.get('odoo_lead_id')}" if d.get("odoo_lead_id") else "",
    ]
    return " | ".join(p for p in partes if p)


def montar_cliente(d):
    """Campos do cliente IXC a partir do payload do Odoo (nomes confirmados na API)."""
    doc = digitos(d.get("cpf_cnpj"))
    cidade_id = d.get("cidade_ixc_id") or ixc.id_cidade(d.get("cidade"))
    if not cidade_id:
        raise ixc.IXCError(f"Cidade '{d.get('cidade')}' não encontrada no IXC")
    return {
        "razao": d.get("nome", ""),
        "cnpj_cpf": doc,
        "tipo_pessoa": "J" if len(doc) > 11 else "F",
        "email": d.get("email", ""),
        "hotsite_email": d.get("email", ""),
        "telefone_celular": d.get("telefone", ""),
        "whatsapp": d.get("telefone", ""),
        "cep": d.get("cep", ""),
        "endereco": d.get("endereco", ""),
        "numero": d.get("numero") or "SN",
        "complemento": d.get("complemento", ""),
        "bairro": d.get("bairro", ""),
        "cidade": cidade_id,
        "id_condominio": d.get("condominio_ixc_id") or "0",
        "bloco": d.get("bloco", ""),
        "apartamento": d.get("apartamento", ""),
        "obs": _obs(d),
        **({"id_vendedor": d["vendedor_ixc_id"]} if d.get("vendedor_ixc_id") else {}),
    }


def virar_prospect(d):
    """Retorna o id do cliente (prospect) no IXC."""
    if not d.get("email"):
        raise ixc.IXCError("E-mail é obrigatório para prospect no IXC")
    campos = montar_cliente(d)
    campos.update(config.IXC_PROSPECT_FIELDS)

    cliente_id = d.get("ixc_cliente_id")
    if not cliente_id:
        # já existe cliente com esse documento? reaproveita em vez de duplicar
        existentes = ixc.listar(config.IXC_TB_CLIENTE, "cnpj_cpf", campos["cnpj_cpf"], rp=1)
        cliente_id = str(existentes[0]["id"]) if existentes else ""

    if cliente_id:
        ixc.alterar(config.IXC_TB_CLIENTE, cliente_id, campos)
    else:
        registro = dict(config.IXC_CLIENTE_DEFAULTS)
        registro.update(campos)
        cliente_id = ixc.inserir(config.IXC_TB_CLIENTE, registro)

    # liga o lead (contato) ao novo prospect
    if d.get("ixc_contato_id"):
        try:
            ixc.alterar(config.IXC_TB_CONTATO, d["ixc_contato_id"], {"id_cliente": cliente_id})
        except Exception as e:
            logger.warning(f"Não consegui vincular contato {d['ixc_contato_id']} ao cliente {cliente_id}: {e}")
    return cliente_id


def criar_negociacao(d):
    """Retorna o id da negociação no IXC (tabela crm_negociacoes, validada em 09/11).

    - Reaproveita negociação ABERTA (status N) do mesmo cliente em vez de duplicar.
    - Endereço: usa o do cliente (considera_endereco_cliente=S, padrão em 98% das negociações).
    - Funil, filial, prioridade e plano de negócio vêm de IXC_NEGOCIACAO_DEFAULTS.
    """
    cliente_id = d.get("ixc_cliente_id")
    if not cliente_id:
        raise ixc.IXCError("Lead ainda não é prospect no IXC")
    neg = dict(config.IXC_NEGOCIACAO_DEFAULTS)
    neg.update({
        "id_cliente": cliente_id,
        "id_contato": d.get("ixc_contato_id") or "0",
        "descricao": d.get("descricao_produto", "") or d.get("plano", ""),
        "obs": f"{_obs(d)} | Plano: {d.get('plano', '')}",
        "data_inicio": _hoje(),
    })
    if d.get("plano_ixc_id"):
        neg["id_plano"] = d["plano_ixc_id"]          # vd_contratos (plano de serviço)
    if d.get("canal_venda_ixc_id"):
        neg["id_canal_origem"] = d["canal_venda_ixc_id"]
    if d.get("funil_ixc_id"):
        neg["id_funil"] = d["funil_ixc_id"]          # funil do time no Odoo (PF/PJ)
    if d.get("valor_esperado"):
        neg["valor_mensal"] = str(d["valor_esperado"])

    neg_id = d.get("ixc_negociacao_id")
    if not neg_id:
        abertas = [n for n in ixc.listar(config.IXC_TB_NEGOCIACAO, "id_cliente", cliente_id, rp=20)
                   if n.get("status") == STATUS_ABERTA]
        neg_id = str(abertas[0]["id"]) if abertas else ""
    if neg_id:
        ixc.alterar(config.IXC_TB_NEGOCIACAO, neg_id, neg)
    else:
        neg_id = ixc.inserir(config.IXC_TB_NEGOCIACAO, neg)

    # marca o prospect como "negociando" no funil do IXC (campo confirmado)
    try:
        ixc.alterar(config.IXC_TB_CLIENTE, cliente_id, {"crm_data_negociando": _hoje()})
    except Exception as e:
        logger.warning(f"Não consegui marcar crm_data_negociando no cliente {cliente_id}: {e}")
    return neg_id


def lead_externo(p):
    """OPA / Chatwoot / landing page: cria no IXC (contato) e no Odoo, com origem."""
    doc = digitos(p.get("cpf_cnpj"))
    contato = {
        "nome": p.get("nome") or "Sem Nome",
        "fone_celular": p.get("telefone", ""),
        "fone_whatsapp": p.get("telefone", ""),
        "email": p.get("email", ""),
        "cnpj_cpf": doc,
        "obs": _obs(p),
    }
    contato_id = ""
    try:
        contato_id = ixc.inserir(config.IXC_TB_CONTATO, contato)
    except Exception as e:
        logger.error(f"Lead externo: falha ao criar contato no IXC ({e}); segue só no Odoo")

    team_id, tag = funil_do_lead(doc)
    ids = {"mf_ixc_contato_id": contato_id}
    extras = {"mf_origem": p.get("origem") or ""}
    pid = upsert_partner(contato["nome"], doc, contato["fone_celular"], contato["email"],
                         ids, f"ID_IXC_LEAD:{contato_id};", {}) if doc else False
    lead_id = upsert_lead(contato["nome"], contato["fone_celular"], contato["email"], pid,
                          doc, _hoje(), team_id, tag, f"ID_IXC_LEAD:{contato_id};",
                          ids, {}, extras=extras)
    return {"ixc_contato_id": contato_id, "odoo_lead_id": lead_id}
