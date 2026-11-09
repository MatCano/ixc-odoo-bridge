"""Cliente da API REST do IXC Provedor.

Convenções da API (confirmadas):
  listar   -> POST /webservice/v1/<tabela>       header "ixcsoft: listar" + qtype/query/oper
  inserir  -> POST /webservice/v1/<tabela>       resposta {"type":"success","id":"10"}
  alterar  -> PUT  /webservice/v1/<tabela>/<id>  com o REGISTRO COMPLETO (por isso lemos antes)
Erros vêm com HTTP 200 e {"type":"error","message":...}.
"""
import logging

import requests

import config

logger = logging.getLogger("IXC-Odoo-Bridge.ixc")


class IXCError(Exception):
    pass


def _url(tabela, rid=None):
    base = f"{config.IXC_HOST}/webservice/v1/{tabela}"
    return f"{base}/{rid}" if rid else base


def _check(resp, acao):
    if resp.status_code != 200:
        raise IXCError(f"{acao}: HTTP {resp.status_code} {resp.text[:300]}")
    try:
        data = resp.json()
    except ValueError:
        raise IXCError(f"{acao}: resposta não-JSON {resp.text[:300]}")
    if str(data.get("type") or data.get("tipo") or "").lower() == "error":
        raise IXCError(f"{acao}: {data.get('message') or data.get('mensagem') or data}")
    return data


def listar(tabela, campo, valor, oper="=", pagina=1, rp=None, sortname=None, sortorder="desc"):
    payload = {
        "qtype": f"{tabela}.{campo}",
        "query": str(valor),
        "oper": oper,
        "page": str(pagina),
        "rp": str(rp or config.PAGE_SIZE),
        "sortname": sortname or f"{tabela}.id",
        "sortorder": sortorder,
    }
    resp = requests.post(
        _url(tabela), json=payload, auth=(config.IXC_USER, config.IXC_PASS),
        headers={"ixcsoft": "listar", "Content-Type": "application/json"}, timeout=20,
    )
    return _check(resp, f"listar {tabela}").get("registros", []) or []


def listar_tudo(tabela, campo="id", valor="0", oper=">"):
    pagina = 1
    while True:
        regs = listar(tabela, campo, valor, oper=oper, pagina=pagina, sortorder="asc")
        yield from regs
        if len(regs) < config.PAGE_SIZE:
            return
        pagina += 1


def buscar(tabela, rid):
    regs = listar(tabela, "id", rid, rp=1)
    return regs[0] if regs else None


def inserir(tabela, registro):
    resp = requests.post(
        _url(tabela), json=registro, auth=(config.IXC_USER, config.IXC_PASS),
        headers={"Content-Type": "application/json"}, timeout=30,
    )
    data = _check(resp, f"inserir {tabela}")
    rid = str(data.get("id") or "")
    if not rid:
        raise IXCError(f"inserir {tabela}: IXC não devolveu id ({data})")
    logger.info(f"IXC: {tabela} {rid} inserido")
    return rid


def alterar(tabela, rid, mudancas):
    """Lê o registro, aplica as mudanças e grava o registro completo (PUT)."""
    atual = buscar(tabela, rid)
    if not atual:
        raise IXCError(f"alterar {tabela}: registro {rid} não encontrado")
    atual.update({k: v for k, v in mudancas.items() if v is not None})
    atual.pop("id", None)
    resp = requests.put(
        _url(tabela, rid), json=atual, auth=(config.IXC_USER, config.IXC_PASS),
        headers={"Content-Type": "application/json"}, timeout=30,
    )
    _check(resp, f"alterar {tabela} {rid}")
    logger.info(f"IXC: {tabela} {rid} alterado ({', '.join(mudancas)})")
    return rid


# ---------------- Cidades (IXC usa ID; Odoo usa nome) ----------------
_cidade_cache = {}


def nome_cidade(cidade_id):
    cidade_id = str(cidade_id or "")
    if not cidade_id or cidade_id == "0":
        return ""
    if cidade_id not in _cidade_cache:
        try:
            reg = buscar(config.IXC_TB_CIDADE, cidade_id) or {}
            _cidade_cache[cidade_id] = reg.get("nome", "")
        except Exception as e:
            logger.warning(f"Cidade {cidade_id}: {e}")
            return ""
    return _cidade_cache[cidade_id]


def id_cidade(nome):
    """Procura a cidade pelo nome. VALIDAR: campo 'nome' da tabela cidade."""
    nome = (nome or "").strip()
    if not nome:
        return ""
    for cid, n in _cidade_cache.items():
        if n.lower() == nome.lower():
            return cid
    regs = listar(config.IXC_TB_CIDADE, "nome", nome, rp=5)
    for r in regs:
        _cidade_cache[str(r.get("id"))] = r.get("nome", "")
    exato = [r for r in regs if (r.get("nome") or "").lower() == nome.lower()]
    escolhido = (exato or regs or [{}])[0]
    return str(escolhido.get("id") or "")
