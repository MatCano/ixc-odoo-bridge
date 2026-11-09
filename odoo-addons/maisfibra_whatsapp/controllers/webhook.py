"""Webhook de entrada da Evolution API.

Configure na Evolution (instância > Webhook):
  URL:     https://<seu-odoo>/maisfibra/whatsapp/webhook?token=<TOKEN>
  Eventos: MESSAGES_UPSERT
  (Webhook by Events: DESLIGADO)
"""
import hashlib
import hmac
import json
import logging

from odoo import http
from odoo.http import Response, request

_logger = logging.getLogger(__name__)


def _extract_text(message):
    message = message or {}
    if message.get("conversation"):
        return message["conversation"]
    ext = message.get("extendedTextMessage") or {}
    if ext.get("text"):
        return ext["text"]
    for kind, label in (
        ("imageMessage", "[imagem]"), ("videoMessage", "[vídeo]"),
        ("documentMessage", "[documento]"), ("audioMessage", "[áudio]"),
        ("stickerMessage", "[figurinha]"), ("locationMessage", "[localização]"),
    ):
        if kind in message:
            caption = (message[kind] or {}).get("caption") or ""
            return f"{label} {caption}".strip()
    return "[mensagem não suportada]"


class WhatsappWebhook(http.Controller):

    @http.route("/maisfibra/whatsapp/webhook", type="http", auth="public",
                methods=["POST"], csrf=False)
    def evolution_webhook(self, **kwargs):
        expected = request.env["ir.config_parameter"].sudo().get_param(
            "maisfibra_whatsapp.webhook_token") or ""
        token = request.httprequest.args.get("token", "")
        if not expected or not hmac.compare_digest(token, expected):
            return Response("unauthorized", status=401)

        try:
            payload = json.loads(request.httprequest.get_data() or b"{}")
        except ValueError:
            return Response("invalid json", status=400)

        event = (payload.get("event") or "").lower().replace("_", ".")
        if event != "messages.upsert":
            return Response("ignored", status=200)

        items = payload.get("data")
        items = items if isinstance(items, list) else [items]
        Message = request.env["maisfibra.whatsapp.message"].sudo()
        provider = request.env["maisfibra.whatsapp.provider"].sudo()

        for data in items:
            if not isinstance(data, dict):
                continue
            key = data.get("key") or {}
            jid = key.get("remoteJid") or ""
            if not jid.endswith("@s.whatsapp.net"):
                continue  # ignora grupos, status e broadcasts
            number = provider.normalize_phone(jid.split("@")[0])
            try:
                Message.process_inbound(
                    number=number,
                    text=_extract_text(data.get("message")),
                    external_id=key.get("id") or "",
                    push_name=data.get("pushName") or "",
                    from_me=bool(key.get("fromMe")),
                )
            except Exception:
                _logger.exception("WhatsApp: falha ao processar mensagem de %s", number)

        return Response("ok", status=200)


class MetaWebhook(http.Controller):
    """Webhook da Meta Cloud API.
    Meta for Developers > WhatsApp > Configuração > Webhook:
      Callback URL: https://<seu-odoo>/maisfibra/whatsapp/meta
      Verify token: o mesmo de Definições > WhatsApp > Meta
      Campo assinado: messages
    """

    @http.route("/maisfibra/whatsapp/meta", type="http", auth="public", methods=["GET"], csrf=False)
    def meta_verify(self, **kw):
        args = request.httprequest.args
        esperado = request.env["ir.config_parameter"].sudo().get_param("maisfibra_whatsapp.meta_verify_token") or ""
        if (args.get("hub.mode") == "subscribe" and esperado
                and hmac.compare_digest(args.get("hub.verify_token", ""), esperado)):
            return Response(args.get("hub.challenge", ""), status=200, content_type="text/plain")
        return Response("forbidden", status=403)

    @http.route("/maisfibra/whatsapp/meta", type="http", auth="public", methods=["POST"], csrf=False)
    def meta_receive(self, **kw):
        ICP = request.env["ir.config_parameter"].sudo()
        bruto = request.httprequest.get_data() or b""
        segredo = ICP.get_param("maisfibra_whatsapp.meta_app_secret") or ""
        if not segredo:
            return Response("not configured", status=403)
        assinatura = request.httprequest.headers.get("X-Hub-Signature-256", "")
        calculada = "sha256=" + hmac.new(segredo.encode(), bruto, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(assinatura, calculada):
            return Response("invalid signature", status=401)
        try:
            payload = json.loads(bruto or b"{}")
        except ValueError:
            return Response("invalid json", status=400)

        Message = request.env["maisfibra.whatsapp.message"].sudo()
        provider = request.env["maisfibra.whatsapp.provider"].sudo()
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                valor = change.get("value") or {}
                nomes = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in valor.get("contacts") or []}
                for msg in valor.get("messages") or []:
                    numero = provider.normalize_phone(msg.get("from"))
                    tipo = msg.get("type")
                    if tipo == "text":
                        texto = (msg.get("text") or {}).get("body", "")
                    elif tipo == "button":
                        texto = (msg.get("button") or {}).get("text", "")
                    elif tipo == "interactive":
                        inter = msg.get("interactive") or {}
                        texto = (inter.get("button_reply") or inter.get("list_reply") or {}).get("title", "")
                    else:
                        texto = f"[{tipo}]"
                    try:
                        Message.process_inbound(number=numero, text=texto, external_id=msg.get("id") or "",
                                                push_name=nomes.get(msg.get("from"), ""), from_me=False)
                    except Exception:
                        _logger.exception("Meta: falha ao processar mensagem de %s", numero)
        return Response("ok", status=200)
