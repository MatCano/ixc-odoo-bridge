"""Validação do IXC — SOMENTE LEITURA.

Rodar no Console do serviço ixc-odoo-bridge (Easypanel), que já tem IXC_HOST/IXC_USER/IXC_PASS.
- Só usa "listar" (header ixcsoft: listar). Nenhum insert/update/delete.
- Não imprime dados de clientes: só nomes de campos, contagens e códigos de configuração.
"""
import collections
import os

import requests

H = os.environ["IXC_HOST"].rstrip("/")
A = (os.environ["IXC_USER"], os.environ["IXC_PASS"])


def listar(t, campo="id", q="0", oper=">", rp=1):
    r = requests.post(
        f"{H}/webservice/v1/{t}",
        json={"qtype": f"{t}.{campo}", "query": q, "oper": oper, "page": "1", "rp": str(rp),
              "sortname": f"{t}.id", "sortorder": "desc"},
        headers={"ixcsoft": "listar", "Content-Type": "application/json"}, auth=A, timeout=20)
    try:
        d = r.json()
    except ValueError:
        return False, f"HTTP {r.status_code} (não-JSON)", []
    if str(d.get("type", "")).lower() == "error":
        return False, (d.get("message") or "")[:120], []
    return True, d.get("total", "?"), d.get("registros") or []


def testar(rotulo, candidatas):
    print(f"\n=== {rotulo} ===")
    for t in candidatas:
        ok, info, regs = listar(t)
        if ok:
            campos = sorted(regs[0].keys()) if regs else []
            print(f"  ✅ {t}: total={info}")
            print(f"     campos: {', '.join(campos)}")
        else:
            print(f"  ❌ {t}: {info}")


testar("Condomínio", ["condominio", "cliente_condominio", "cad_condominio"])
testar("Negociação (CRM)", ["crm_negociacoes", "crm_negociacao", "cliente_negociacao", "crm_negociacao_cliente"])
testar("Plano de negociação", ["crm_planos_negociacao", "crm_plano_negociacao", "crm_planos", "crm_negociacao_planos"])
testar("Cidade", ["cidade"])

print("\n=== Prospects (clientes com crm = S, últimos 50) ===")
ok, info, regs = listar("cliente", "crm", "S", "=", rp=50)
if ok:
    print(f"  total com crm=S: {info}")
    for campo in ("status_prospeccao", "ativo", "filial_id", "id_tipo_cliente", "tipo_assinante",
                  "tipo_cliente_scm", "cond_pagamento", "tipo_localidade", "contribuinte_icms"):
        cont = collections.Counter(str(r.get(campo, "")) for r in regs)
        print(f"  {campo}: {dict(cont.most_common(5))}")
    ganhos = sum(1 for r in regs if r.get("crm_data_vencemos") not in (None, "", "0000-00-00"))
    perdas = sum(1 for r in regs if r.get("crm_data_perdemos") not in (None, "", "0000-00-00"))
    print(f"  com crm_data_vencemos: {ganhos} | com crm_data_perdemos: {perdas}")
else:
    print(f"  ❌ {info}")

print("\nFim. Nada foi alterado no IXC.")
