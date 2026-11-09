"""Alertas da integração para a equipe técnica.

Posta no canal do Odoo Discuss (padrão: "Integração IXC") quando uma rotina falha
e de novo quando ela volta ao normal. Mesma falha só é repetida depois de
ALERTA_INTERVALO_MIN minutos, para não virar spam.
"""
import logging
import os
from datetime import datetime, timedelta

from odoo import conexao, resetar_conexao

logger = logging.getLogger("IXC-Odoo-Bridge.alertas")

ATIVO = os.getenv("ALERTAS", "1") == "1"
CANAL = os.getenv("ALERTA_CANAL", "Integração IXC")
INTERVALO = timedelta(minutes=int(os.getenv("ALERTA_INTERVALO_MIN", "60")))

# chave -> {"ativo": bool, "ultimo_envio": datetime|None, "ultima_falha": str, "desde": datetime|None}
estado = {}


def _canal_id(odoo):
    ids = odoo.search("discuss.channel", [["name", "=", CANAL]], limit=1)
    if ids:
        return ids[0]
    return odoo.create("discuss.channel", {"name": CANAL, "channel_type": "channel",
                                           "description": "Alertas automáticos do ixc-odoo-bridge"})


def _postar(texto):
    if not ATIVO:
        return
    try:
        odoo = conexao()
        cid = _canal_id(odoo)
        odoo.call("discuss.channel", "message_post", [[cid]], {
            "body": texto, "message_type": "comment", "subtype_xmlid": "mail.mt_comment",
        })
    except Exception as e:  # Odoo fora do ar: só o log registra
        logger.error(f"Não consegui postar alerta no Odoo ({e}). Alerta: {texto}")
        resetar_conexao()


def falha(chave, mensagem):
    agora = datetime.now()
    e = estado.setdefault(chave, {"ativo": False, "ultimo_envio": None, "ultima_falha": "", "desde": None})
    e["ultima_falha"] = mensagem
    if not e["ativo"]:
        e["desde"] = agora
    deve_avisar = not e["ativo"] or not e["ultimo_envio"] or agora - e["ultimo_envio"] >= INTERVALO
    e["ativo"] = True
    logger.warning(f"[alerta] {chave}: {mensagem}")
    if deve_avisar:
        e["ultimo_envio"] = agora
        desde = e["desde"].strftime("%d/%m %H:%M")
        _postar(f"⚠️ <b>Integração IXC — {chave}</b>: {mensagem}<br/>Falhando desde {desde}. "
                f"Ver logs do serviço ixc-odoo-bridge no Easypanel.")


def ok(chave):
    e = estado.get(chave)
    if e and e["ativo"]:
        e["ativo"] = False
        _postar(f"✅ <b>Integração IXC — {chave}</b>: normalizado.")
        logger.info(f"[alerta] {chave}: normalizado")


def resumo():
    return {k: {"falhando": v["ativo"], "ultima_falha": v["ultima_falha"]} for k, v in estado.items()}
