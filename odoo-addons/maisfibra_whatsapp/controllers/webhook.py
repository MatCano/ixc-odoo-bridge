"""Webhooks de entrada para WhatsApp (WhatsApp Web via Chromium, Evolution API e Meta)."""
import base64
import hashlib
import hmac
import json
import logging
import os
import mimetypes
import sqlite3
from markupsafe import Markup

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


class WhatsappChromiumInbound(http.Controller):
    """Inbound do serviço WhatsApp Web via Chromium.
    Notifica mensagens recebidas e enviadas para o SQLite, contatos e o Discuss."""

    @http.route("/media/<path:subpath>", type="http", auth="public", methods=["GET"], csrf=False)
    def serve_whatsapp_media(self, subpath, **kwargs):
        file_path = os.path.join("/var/lib/odoo/whatsapp/media", subpath)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            mime, _ = mimetypes.guess_type(file_path)
            with open(file_path, "rb") as f:
                return Response(f.read(), mimetype=mime or "application/octet-stream")
        return Response("Not found", status=404)

    @http.route("/maisfibra/whatsapp/session_status", type="http", auth="user", methods=["GET"], csrf=False)
    def session_status(self, **kwargs):
        provider = request.env["maisfibra.whatsapp.provider"]
        status = provider.get_user_session_status(request.env.user.id)
        return Response(json.dumps(status), status=200, content_type="application/json")

    @http.route("/maisfibra/whatsapp/inbound", type="http", auth="public", methods=["POST"], csrf=False)
    def chromium_inbound(self, **kwargs):
        try:
            payload = json.loads(request.httprequest.get_data() or b"{}")
        except ValueError:
            return Response(json.dumps({"error": "invalid json"}), status=400, content_type="application/json")

        user_raw = str(payload.get("user_id") or payload.get("userId") or "")
        try:
            uid = int(user_raw.replace("user_", "")) if user_raw else 1
        except ValueError:
            uid = 1

        phone = payload.get("phone") or (payload.get("chat_id") or "").replace("@c.us", "")
        chat_id = payload.get("chat_id") or f"{phone}@c.us"

        # Ignora mensagens de grupos ou transmissões
        if "@g.us" in chat_id or "@g.us" in phone or "status@broadcast" in chat_id:
            return Response(json.dumps({"status": "ignored", "reason": "group message"}), status=200, content_type="application/json")

        body = payload.get("body") or ""
        media_type = payload.get("type") or payload.get("mediaType") or "text"
        media_url = payload.get("media_url") or payload.get("mediaUrl")
        external_id = payload.get("message_id") or payload.get("messageId") or ""
        from_me = bool(payload.get("from_me") or payload.get("fromMe"))
        sender_name = payload.get("senderName") or payload.get("sender") or ""

        # NUNCA permitir que o remetente seja nomeado como user_5 ou JID
        if sender_name in (f"user_{uid}", "user_5") or "@" in sender_name:
            sender_name = ""

        Message = request.env["maisfibra.whatsapp.message"].sudo()
        provider = request.env["maisfibra.whatsapp.provider"].sudo()
        User = request.env["res.users"].sudo()
        Partner = request.env["res.partner"].sudo()
        Channel = request.env["discuss.channel"].sudo()

        # Se o telefone veio como LID (ex: 15 dígitos sem 55), tenta resolver o telefone real no SQLite
        clean_phone = provider.normalize_phone(phone)
        if len(clean_phone) > 13 or (not clean_phone.startswith("55") and len(clean_phone) >= 12):
            conn = provider._get_sqlite_conn()
            if conn:
                try:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT number, name FROM whatsapp_contacts 
                        WHERE user_id = ? AND (lid LIKE ? OR jid LIKE ? OR number LIKE ?)
                        LIMIT 1
                    """, (f"user_{uid}", f"%{clean_phone}%", f"%{clean_phone}%", f"%{clean_phone}%"))
                    row = cur.fetchone()
                    if row and row[0] and not row[0].startswith("1"):
                        clean_phone = provider.normalize_phone(row[0])
                        if not sender_name and row[1]:
                            sender_name = row[1]
                except Exception as db_err:
                    _logger.warning("Erro ao resolver LID no SQLite: %s", db_err)
                finally:
                    conn.close()

        if not clean_phone:
            return Response(json.dumps({"status": "ignored", "reason": "invalid phone"}), status=200, content_type="application/json")

        # Procura parceiro existente pelo telefone cadastrado
        partner = Message._find_partner(clean_phone)
        if not partner:
            # Não cria parceiros com nomes lixo ou IDs
            final_name = sender_name or clean_phone
            if "@" in final_name or final_name.startswith("user_"):
                final_name = clean_phone

            partner = Partner.create({
                "name": final_name,
                "phone": clean_phone,
                "comment": f"Contato de WhatsApp ({chat_id})"
            })

        user = User.browse(uid).exists() or User.browse(1)

        # Processa anexos de mídia (áudio/voz, imagem, vídeo ou documento)
        attachment_ids = []
        media_base64 = payload.get("media_base64")
        media_mime = payload.get("media_mime")
        media_filename = payload.get("media_filename")

        if not media_base64 and media_url:
            local_media_path = os.path.join("/var/lib/odoo/whatsapp", media_url.lstrip("/"))
            if os.path.exists(local_media_path) and os.path.isfile(local_media_path):
                try:
                    with open(local_media_path, "rb") as mf:
                        media_base64 = base64.b64encode(mf.read()).decode()
                    if not media_mime:
                        media_mime, _ = mimetypes.guess_type(local_media_path)
                    if not media_filename:
                        media_filename = os.path.basename(local_media_path)
                except Exception as read_err:
                    _logger.warning("Erro ao ler mídia do disco local: %s", read_err)

        user_partner = user.partner_id
        dest_partner = partner.sudo()

        # Cria ou obtém canal direto no Discuss com o parceiro identificado
        try:
            channel = Channel.sudo().search([
                ("channel_type", "=", "chat"),
                ("channel_member_ids.partner_id", "=", user_partner.id),
            ]).filtered(
                lambda c: dest_partner.id in c.channel_member_ids.mapped("partner_id").ids
            )[:1]

            if not channel:
                channel = Channel.sudo().with_user(user)._get_or_create_chat(partners_to=[dest_partner.id], pin=True).sudo()

            # Garante que o canal está pinado para o usuário
            member = channel.channel_member_ids.filtered(lambda m: m.partner_id.id == user_partner.id)
            if member:
                member.sudo().write({"unpin_dt": False})

            # Cria anexo nativo do Odoo se for mídia
            if media_base64 and media_mime:
                try:
                    ext = media_filename.split(".")[-1] if media_filename and "." in media_filename else "bin"
                    att_name = media_filename or f"whatsapp_{external_id}.{ext}"
                    att = request.env["ir.attachment"].sudo().create({
                        "name": att_name,
                        "datas": media_base64,
                        "mimetype": media_mime,
                        "res_model": "discuss.channel",
                        "res_id": channel.id,
                    })
                    attachment_ids.append(att.id)
                except Exception as att_err:
                    _logger.warning("Erro ao criar ir.attachment para Discuss: %s", att_err)

            # Posta no canal do Discuss
            author = user_partner if from_me else dest_partner
            channel.sudo().with_context(wa_skip_outbound=True, mail_create_nolog=True).message_post(
                body=body or "",
                attachment_ids=attachment_ids,
                author_id=author.id,
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )
        except Exception as ex:
            _logger.exception("Falha ao postar no canal Discuss: %s", ex)

        # Processa mensagem no modelo CRM / Chatter
        try:
            Message.process_inbound(
                number=clean_phone,
                text=body or f"[{media_type}]",
                external_id=external_id,
                push_name=partner.name,
                from_me=from_me,
            )
        except Exception as ex:
            _logger.exception("Falha ao registrar mensagem recebida de %s: %s", clean_phone, ex)

        return Response(json.dumps({"status": "success", "partner_id": partner.id}), status=200, content_type="application/json")


class WhatsappWebhook(http.Controller):
    """Webhook legado da Evolution API."""

    @http.route("/maisfibra/whatsapp/webhook", type="http", auth="public", methods=["POST"], csrf=False)
    def evolution_webhook(self, **kwargs):
        expected = request.env["ir.config_parameter"].sudo().get_param("maisfibra_whatsapp.webhook_token") or ""
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
                continue
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
    """Webhook da Meta Cloud API."""

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
