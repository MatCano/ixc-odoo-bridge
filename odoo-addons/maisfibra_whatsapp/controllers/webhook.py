"""Webhook de entrada da Evolution API.

Configure na Evolution (instância > Webhook):
  URL:     https://<seu-odoo>/maisfibra/whatsapp/webhook?token=<TOKEN>
  Eventos: MESSAGES_UPSERT
  (Webhook by Events: DESLIGADO)
"""
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
