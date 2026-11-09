"""Validação do IXC, parte 2 — SOMENTE LEITURA (só 'listar'; não imprime dados de clientes)."""
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
        return False, f"HTTP {r.status_code}", []
    if str(d.get("type", "")).lower() == "error":
        return False, (d.get("message") or "")[:100], []
    return True, d.get("total", "?"), d.get("registros") or []


print("=== Plano de negociação (candidatas) ===")
for t in ["crm_plano_negocio", "crm_planos_negocio", "crm_planos_negocios", "plano_negocio",
          "crm_plano", "vd_contratos", "vd_contrato", "crm_negociacoes_plano"]:
    ok, info, regs = listar(t)
    if ok:
        print(f"  ✅ {t}: total={info} | campos: {', '.join(sorted(regs[0].keys())) if regs else '-'}")
    else:
        print(f"  ❌ {t}: {info}")

print("\n=== Negociações: códigos usados (últimas 200) ===")
ok, info, regs = listar("crm_negociacoes", rp=200)
if ok:
    for campo in ("status", "substatus", "id_estagio", "id_funil", "id_plano", "id_plano_negocio",
                  "id_canal_origem", "origem", "id_filial", "considera_endereco_cliente", "prioridade"):
        print(f"  {campo}: {dict(collections.Counter(str(r.get(campo, '')) for r in regs).most_common(8))}")
    vazio = (None, "", "0000-00-00", "0000-00-00 00:00:00")
    print(f"  com data_vencemos: {sum(1 for r in regs if r.get('data_vencemos') not in vazio)}"
          f" | com data_perdemos: {sum(1 for r in regs if r.get('data_perdemos') not in vazio)}")
    print(f"  ultima_atualizacao (mais recente): {max((r.get('ultima_atualizacao') or '') for r in regs)}")

print("\n=== Estágios / funis do CRM (candidatas) ===")
for t in ["crm_estagio", "crm_estagios", "crm_funil", "crm_funis", "crm_negociacoes_estagio"]:
    ok, info, regs = listar(t, rp=20)
    if ok:
        nomes = [str(r.get("descricao") or r.get("estagio") or r.get("nome") or r.get("funil") or r.get("id")) for r in regs]
        print(f"  ✅ {t}: total={info} | {', '.join(nomes[:20])}")
    else:
        print(f"  ❌ {t}: {info}")

print("\nFim. Nada foi alterado no IXC.")
