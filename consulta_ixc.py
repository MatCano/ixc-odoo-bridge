"""Consulta em tempo real (somente leitura) de contratos e financeiro do cliente no IXC.

Tabelas confirmadas na API: cliente_contrato e fn_areceber.
Os códigos de status abaixo seguem o padrão do IXC — VALIDAR com um cliente real.
"""
from datetime import date

import config
import ixc
from ixc_para_odoo import digitos

STATUS_CONTRATO = {
    "A": "Ativo", "I": "Inativo", "N": "Negativado", "P": "Pré-contrato", "D": "Desistiu",
}
STATUS_INTERNET = {
    "A": "Ativo", "D": "Desativado", "CM": "Bloqueio manual", "CA": "Bloqueio automático",
    "FA": "Financeiro em atraso", "AA": "Aguardando assinatura",
}
STATUS_FATURA = {"A": "Em aberto", "R": "Recebido", "P": "Parcial", "C": "Cancelado"}

IXC_TB_CONTRATO = config.IXC_TB_CONTRATO
IXC_TB_ARECEBER = config.IXC_TB_ARECEBER
MAX_FATURAS = 24


def _f(v):
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _resolver_cliente(ixc_cliente_id, cpf_cnpj):
    if ixc_cliente_id:
        return str(ixc_cliente_id)
    doc = digitos(cpf_cnpj)
    if not doc:
        raise ixc.IXCError("Informe o ID do cliente IXC ou o CPF/CNPJ")
    regs = ixc.listar(config.IXC_TB_CLIENTE, "cnpj_cpf", doc, rp=1)
    if not regs:
        # o IXC às vezes guarda o documento formatado
        fmt = (f"{doc[:3]}.{doc[3:6]}.{doc[6:9]}-{doc[9:]}" if len(doc) == 11 else
               f"{doc[:2]}.{doc[2:5]}.{doc[5:8]}/{doc[8:12]}-{doc[12:]}")
        regs = ixc.listar(config.IXC_TB_CLIENTE, "cnpj_cpf", fmt, rp=1)
    if not regs:
        raise ixc.IXCError("Cliente não encontrado no IXC")
    return str(regs[0]["id"])


def consultar(ixc_cliente_id="", cpf_cnpj=""):
    cliente_id = _resolver_cliente(ixc_cliente_id, cpf_cnpj)
    hoje = date.today().isoformat()

    contratos = []
    for c in ixc.listar(IXC_TB_CONTRATO, "id_cliente", cliente_id, rp=50):
        contratos.append({
            "ixc_id": str(c.get("id")),
            "nome": c.get("contrato") or c.get("descricao_aux_plano_venda") or f"Contrato {c.get('id')}",
            "status": STATUS_CONTRATO.get(c.get("status"), c.get("status") or ""),
            "status_internet": STATUS_INTERNET.get(c.get("status_internet"), c.get("status_internet") or ""),
            "data_ativacao": c.get("data_ativacao") or "",
        })

    faturas = []
    regs = ixc.listar(IXC_TB_ARECEBER, "id_cliente", cliente_id, rp=MAX_FATURAS,
                      sortname=f"{IXC_TB_ARECEBER}.data_vencimento", sortorder="desc")
    for f in regs:
        aberto = f.get("status") in ("A", "P")
        venc = f.get("data_vencimento") or ""
        faturas.append({
            "ixc_id": str(f.get("id")),
            "vencimento": venc,
            "valor": _f(f.get("valor")),
            "valor_aberto": _f(f.get("valor_aberto")) if aberto else 0.0,
            "status": STATUS_FATURA.get(f.get("status"), f.get("status") or ""),
            "vencida": bool(aberto and venc and venc[:10] < hoje),
            "linha_digitavel": f.get("linha_digitavel") or "",
        })

    abertas = [f for f in faturas if f["valor_aberto"] > 0]
    vencidas = [f for f in abertas if f["vencida"]]
    internet = [c["status_internet"] for c in contratos if c["status"] == "Ativo"]
    return {
        "ixc_cliente_id": cliente_id,
        "contratos": contratos,
        "faturas": faturas,
        "resumo": {
            "status_servico": ", ".join(sorted(set(internet))) or ("Sem contrato ativo" if contratos else "Sem contrato"),
            "faturas_abertas": len(abertas),
            "faturas_vencidas": len(vencidas),
            "valor_aberto": round(sum(f["valor_aberto"] for f in abertas), 2),
            "valor_vencido": round(sum(f["valor_aberto"] for f in vencidas), 2),
        },
    }
