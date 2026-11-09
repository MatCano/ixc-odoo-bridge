"""Sentido IXC -> Odoo (polling).

- Leads (/contato) e clientes (/cliente) viram contato + oportunidade no Odoo
- Endereço, condomínio, bloco e apartamento vêm junto
- Ganho/perda: datas crm_data_vencemos / crm_data_perdemos do cliente movem o lead
- Cadastros: condomínios e planos de negociação
"""
import json
import logging
import re
from datetime import datetime, timedelta

import alertas
import config
import ixc
from odoo import conexao, resetar_conexao

logger = logging.getLogger("IXC-Odoo-Bridge.sync")


def digitos(v):
    return re.sub(r"\D", "", v or "")


def _vazio(v):
    return v in (None, "", "0", "0000-00-00", "0000-00-00 00:00:00")


# ======================================================================
# Estado entre ciclos (watermark + ids processados)
# ======================================================================
def carregar_estado(ns):
    try:
        with open(config.SYNC_STATE_PATH, encoding="utf-8") as f:
            bloco = json.load(f).get(ns, {})
        return bloco.get("ultima", ""), set(bloco.get("processados", []))
    except Exception:
        return "", set()


def salvar_estado(ns, ultima, processados):
    import os
    try:
        os.makedirs(os.path.dirname(config.SYNC_STATE_PATH), exist_ok=True)
        try:
            with open(config.SYNC_STATE_PATH, encoding="utf-8") as f:
                estado = json.load(f)
        except Exception:
            estado = {}
        # mantém só os ids mais recentes (a lista não cresce para sempre)
        ids = sorted(processados, key=lambda x: int(x) if x.isdigit() else 0)[-config.MAX_PROCESSADOS:]
        estado[ns] = {"ultima": ultima, "processados": ids}
        with open(config.SYNC_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(estado, f)
    except Exception as e:
        logger.warning(f"Não foi possível salvar estado ({ns}): {e}")


# ======================================================================
# Roteamento de funil (regra original)
# ======================================================================
def funil_do_lead(documento):
    if len(digitos(documento)) > 11:
        return config.FUNIL_ODOO["B2B"], "[B2B]"
    return config.FUNIL_ODOO["B2C"], "[B2C]"


def roteamento_cliente(tipo_pessoa, documento, ativo, status_prospeccao, data_cadastro):
    hoje = datetime.now(config.TZ_BRASIL).date()
    cutoff = (hoje - timedelta(days=config.DELTA_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    eh_recente = bool(data_cadastro) and data_cadastro >= cutoff
    if tipo_pessoa == "J" or len(digitos(documento)) > 11:
        return True, config.FUNIL_ODOO["B2B"], "[B2B]"
    if status_prospeccao == "P" or eh_recente or ativo != "S":
        return True, config.FUNIL_ODOO["B2C"], "[B2C]"
    return False, None, "[CONTATO]"


# ======================================================================
# Endereço/condomínio vindos do IXC -> campos mf_* do Odoo
# ======================================================================
def campos_endereco(reg):
    if not config.MF_CAMPOS:
        return {}
    vals = {
        "mf_endereco": reg.get("endereco") or "",
        "mf_numero": reg.get("numero") or "",
        "mf_complemento": reg.get("complemento") or "",
        "mf_bairro": reg.get("bairro") or "",
        "mf_cep": reg.get("cep") or "",
        "mf_bloco": reg.get("bloco") or "",
        "mf_apartamento": "" if _vazio(reg.get("apartamento")) else reg.get("apartamento"),
        "mf_cidade": ixc.nome_cidade(reg.get("cidade")),
    }
    id_cond = reg.get("id_condominio")
    if not _vazio(id_cond):
        ids = conexao().search("maisfibra.condominio", [["ixc_id", "=", str(id_cond)]], limit=1)
        if ids:
            vals["mf_condominio_id"] = ids[0]
    return {k: v for k, v in vals.items() if v}


def _so_preencher_vazios(model, rid, vals):
    """Em registro existente, só preenche o que está vazio no Odoo (não pisa em edição do vendedor)."""
    if not vals:
        return {}
    atual = conexao().read(model, [rid], list(vals))[0]
    return {k: v for k, v in vals.items() if not atual.get(k)}


# ======================================================================
# Upserts no Odoo
# ======================================================================
def upsert_partner(nome, documento, telefone, email, ids_ixc, marcador, endereco):
    odoo = conexao()
    doc = digitos(documento)
    domain = ["|", ["vat", "=", doc], ["email", "=", email]] if email else [["vat", "=", doc]]
    existente = odoo.search("res.partner", domain, limit=1)

    eh_empresa = len(doc) > 11
    vals = {
        "name": nome, "phone": telefone, "email": email, "vat": doc,
        "company_type": "company" if eh_empresa else "person", "is_company": eh_empresa,
    }
    if config.MF_CAMPOS:
        vals.update({k: v for k, v in ids_ixc.items() if v})
    else:
        vals["comment"] = marcador

    if existente:
        pid = existente[0]
        vals.update(_so_preencher_vazios("res.partner", pid, endereco))
        odoo.write("res.partner", [pid], vals)
        return pid
    vals.update(endereco)
    return odoo.create("res.partner", vals)


def buscar_lead(doc, ids_ixc, legacy_marker=None):
    odoo = conexao()
    if config.MF_CAMPOS:
        for campo in ("mf_ixc_cliente_id", "mf_ixc_contato_id"):
            if ids_ixc.get(campo):
                ids = odoo.search("crm.lead", [[campo, "=", ids_ixc[campo]]], limit=1)
                if ids:
                    return ids[0]
        if doc:
            ids = odoo.search("crm.lead", [["mf_cpf_cnpj", "=", doc]], limit=1)
            if ids:
                return ids[0]
    # compatibilidade com leads criados pela versão antiga (marcadores na descrição)
    domain = [["description", "ilike", f"DOC:{doc};"]] if doc else []
    if legacy_marker:
        domain = (["|"] + domain if domain else []) + [["description", "ilike", legacy_marker]]
    if not domain:
        return None
    ids = odoo.search("crm.lead", domain, limit=1)
    return ids[0] if ids else None


def upsert_lead(nome, telefone, email, partner_id, documento, data_cadastro, team_id, tag,
                fonte_ref, ids_ixc, endereco, extras=None, legacy_marker=None):
    odoo = conexao()
    doc = digitos(documento)
    lead_id = buscar_lead(doc, ids_ixc, legacy_marker)
    vals = {
        "contact_name": nome, "partner_id": partner_id,
        "email_from": email, "phone": telefone,
    }
    if config.MF_CAMPOS:
        vals.update({k: v for k, v in ids_ixc.items() if v})
        vals["mf_cpf_cnpj"] = doc
        vals.update(extras or {})

    if lead_id:
        vals.update(_so_preencher_vazios("crm.lead", lead_id, endereco))
        odoo.write("crm.lead", [lead_id], vals)
        logger.info(f"Oportunidade atualizada ({tag}): {nome}")
        return lead_id

    vals.update(endereco)
    vals.update({
        "name": f"{tag} - {nome}",
        "team_id": team_id,
        "type": "opportunity",
        "description": f"DOC:{doc}; {fonte_ref}\nDocumento: {documento}\nCadastro: {data_cadastro}",
    })
    lead_id = odoo.create("crm.lead", vals)
    logger.info(f"Oportunidade criada {lead_id} ({tag}): {nome}")
    return lead_id


# ======================================================================
# Processadores
# ======================================================================
def processar_contato(lead):
    try:
        id_lead = str(lead.get("id") or lead.get("id_ixc") or "")
        nome = lead.get("nome") or lead.get("razao") or "Sem Nome"
        documento = lead.get("cnpj_cpf", "")
        telefone = (lead.get("fone_celular") or lead.get("fone_whatsapp")
                    or lead.get("fone_comercial") or lead.get("telefone") or "")
        email = lead.get("email", "")

        if not _vazio(lead.get("id_cliente")):
            logger.info(f"Contato IXC {id_lead} já é cliente: tratado pela fonte /cliente.")
            return True
        if not digitos(documento):
            logger.info(f"Lead IXC {id_lead} ignorado (sem CPF/CNPJ): {nome}")
            return True

        team_id, tag = funil_do_lead(documento)
        ids = {"mf_ixc_contato_id": id_lead}
        endereco = campos_endereco(lead)
        # (corrigido: antes o lead recebia também ID_IXC_CLI com o id do LEAD)
        pid = upsert_partner(nome, documento, telefone, email, ids, f"ID_IXC_LEAD:{id_lead};", endereco)
        upsert_lead(nome, telefone, email, pid, documento, lead.get("data_cadastro", ""),
                    team_id, tag, f"ID_IXC_LEAD:{id_lead};", ids, endereco)
        return True
    except Exception as e:
        logger.error(f"Erro ao processar lead IXC: {e}")
        resetar_conexao()
        return False


def processar_cliente(cli):
    try:
        id_cli = str(cli.get("id") or "")
        nome = cli.get("razao") or cli.get("nome") or "Sem Nome"
        documento = cli.get("cnpj_cpf", "")
        telefone = cli.get("telefone_celular") or cli.get("whatsapp") or cli.get("telefone_comercial") or ""
        email = cli.get("email", "")
        if not digitos(documento):
            logger.info(f"Cliente IXC {id_cli} ignorado (sem CPF/CNPJ): {nome}")
            return True

        ids = {"mf_ixc_cliente_id": id_cli}
        endereco = campos_endereco(cli)
        pid = upsert_partner(nome, documento, telefone, email, ids, f"ID_IXC_CLI:{id_cli};", endereco)

        deve, team_id, tag = roteamento_cliente(
            cli.get("tipo_pessoa", "F"), documento, cli.get("ativo", "N"),
            cli.get("status_prospeccao", ""), cli.get("data_cadastro", ""),
        )
        if not deve:
            return True
        upsert_lead(nome, telefone, email, pid, documento, cli.get("data_cadastro", ""),
                    team_id, tag, f"ID_IXC_CLI:{id_cli};", ids, endereco,
                    legacy_marker=f"ID_IXC:{id_cli};")
        return True
    except Exception as e:
        logger.error(f"Erro ao processar cliente IXC: {e}")
        resetar_conexao()
        return False


# ======================================================================
# Varredura incremental genérica
# ======================================================================
def sincronizar_fonte(tabela, campo_data, ns, processar, data_only, lookback, reprocessar=False):
    try:
        agora = datetime.now(config.TZ_BRASIL)
        fmt = "%Y-%m-%d" if data_only else "%Y-%m-%d %H:%M:%S"
        agora_ref = agora.strftime(fmt)
        delta = timedelta(days=lookback) if data_only else timedelta(hours=lookback)
        watermark, processados = carregar_estado(ns)
        if data_only and len(watermark) > 10:
            watermark = ""
        if not watermark:
            watermark = (agora - delta).strftime(fmt)
            logger.info(f"{ns}: backfill desde {watermark}")

        pagina, novos, falhas, max_data = 1, 0, 0, watermark
        erro_ixc = ""
        while True:
            try:
                regs = ixc.listar(tabela, campo_data, watermark, oper=">=", pagina=pagina)
            except Exception as e:
                logger.error(f"{ns}: falha ao listar {tabela}: {e}")
                erro_ixc = str(e)[:300]
                break
            for item in regs:
                rid = str(item.get("id"))
                if not reprocessar and rid in processados:
                    continue
                if processar(item):
                    processados.add(rid)
                    ts = str(item.get(campo_data) or "")
                    if ts and not ts.startswith("0000") and ts > max_data:
                        max_data = ts
                    novos += 1
                else:
                    falhas += 1
            if len(regs) < config.PAGE_SIZE:
                break
            pagina += 1

        max_data = min(max_data, agora_ref)
        salvar_estado(ns, max_data, set() if reprocessar else processados)
        logger.info(f"{ns}: {novos} processados, {falhas} falhas (watermark {max_data})")
        if erro_ixc:
            alertas.falha(f"sincronismo {ns}", f"não consegui ler o IXC ({tabela}): {erro_ixc}")
        elif falhas:
            alertas.falha(f"sincronismo {ns}", f"{falhas} registro(s) não foram gravados no Odoo neste ciclo")
        else:
            alertas.ok(f"sincronismo {ns}")
    except Exception as e:
        logger.error(f"Falha no ciclo {ns}: {e}")
        alertas.falha(f"sincronismo {ns}", f"ciclo interrompido: {str(e)[:300]}")


def sync_leads():
    sincronizar_fonte(config.IXC_TB_CONTATO, "data_cadastro", "lead", processar_contato,
                      data_only=False, lookback=config.DELTA_LOOKBACK_HOURS)


def sync_clientes():
    sincronizar_fonte(config.IXC_TB_CLIENTE, "data_cadastro", "cliente", processar_cliente,
                      data_only=True, lookback=config.DELTA_LOOKBACK_DAYS)


# ======================================================================
# Ganho / perda (IXC -> estágio do Odoo)
# ======================================================================
def processar_status(cli):
    """Cliente alterado no IXC: se marcou vencemos/perdemos, move o lead no Odoo."""
    if not config.MF_CAMPOS:
        return True
    try:
        if not _vazio(cli.get("crm_data_vencemos")):
            codigo = "aguardando_assinatura"
        elif not _vazio(cli.get("crm_data_perdemos")):
            codigo = "perdemos"
        else:
            return True
        odoo = conexao()
        leads = odoo.search("crm.lead", [["mf_ixc_cliente_id", "=", str(cli.get("id"))]], limit=1)
        if not leads:
            return True
        lead = odoo.read("crm.lead", leads, ["stage_id", "team_id"])[0]
        team_id = lead["team_id"][0] if lead.get("team_id") else None
        stage_id = odoo.stage_por_codigo(codigo, team_id)
        if stage_id and (not lead.get("stage_id") or lead["stage_id"][0] != stage_id):
            odoo.write("crm.lead", leads, {"stage_id": stage_id})
            odoo.call("crm.lead", "message_post", [leads], {
                "body": f"IXC: negociação {'GANHA' if codigo != 'perdemos' else 'PERDIDA'} — lead movido.",
                "subtype_xmlid": "mail.mt_note",
            })
            logger.info(f"Lead {leads[0]} -> {codigo} (cliente IXC {cli.get('id')})")
        return True
    except Exception as e:
        logger.error(f"Erro ao processar status do cliente {cli.get('id')}: {e}")
        resetar_conexao()
        return False


def sync_status():
    sincronizar_fonte(config.IXC_TB_CLIENTE, "ultima_atualizacao", "status", processar_status,
                      data_only=False, lookback=config.DELTA_LOOKBACK_HOURS, reprocessar=True)


# ======================================================================
# Cadastros: condomínios e planos
# ======================================================================
def _upsert_por_ixc_id(model, ixc_id, vals):
    odoo = conexao()
    ids = odoo.search(model, [["ixc_id", "=", ixc_id], ["active", "in", [True, False]]], limit=1)
    if ids:
        odoo.write(model, ids, vals)
    else:
        vals["ixc_id"] = ixc_id
        odoo.create(model, vals)


def sync_condominios():
    if not config.MF_CAMPOS:
        return
    n = 0
    try:
        for r in ixc.listar_tudo(config.IXC_TB_CONDOMINIO):
            nome = r.get("condominio") or r.get("nome") or r.get("descricao")  # VALIDAR
            if not nome:
                continue
            _upsert_por_ixc_id("maisfibra.condominio", str(r["id"]), {
                "name": nome,
                "endereco": r.get("endereco") or "",
                "numero": r.get("numero") or "",
                "bairro": r.get("bairro") or "",
                "cep": r.get("cep") or "",
                "cidade": ixc.nome_cidade(r.get("cidade")),
                "ixc_cidade_id": str(r.get("cidade") or ""),
            })
            n += 1
        logger.info(f"Condomínios sincronizados: {n}")
        alertas.ok("condomínios")
    except Exception as e:
        logger.error(f"Falha ao sincronizar condomínios ({config.IXC_TB_CONDOMINIO}): {e}")
        alertas.falha("condomínios", f"tabela {config.IXC_TB_CONDOMINIO}: {str(e)[:300]}")
        resetar_conexao()


def sync_planos():
    if not config.MF_CAMPOS:
        return
    n = 0
    try:
        for r in ixc.listar_tudo(config.IXC_TB_PLANO):
            nome = r.get("descricao") or r.get("nome") or r.get("plano")  # VALIDAR
            if not nome:
                continue
            _upsert_por_ixc_id("maisfibra.plano", str(r["id"]), {
                "name": nome, "active": r.get("ativo", "S") != "N",
            })
            n += 1
        logger.info(f"Planos sincronizados: {n}")
        alertas.ok("planos")
    except Exception as e:
        logger.error(f"Falha ao sincronizar planos ({config.IXC_TB_PLANO}): {e}")
        alertas.falha("planos", f"tabela {config.IXC_TB_PLANO}: {str(e)[:300]}")
        resetar_conexao()
