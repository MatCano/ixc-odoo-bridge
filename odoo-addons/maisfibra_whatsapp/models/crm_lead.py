import re

from odoo import _, fields, models
from odoo.exceptions import UserError


class CrmLead(models.Model):
    _inherit = "crm.lead"

    whatsapp_message_ids = fields.One2many("maisfibra.whatsapp.message", "lead_id", string="Mensagens WhatsApp")
    whatsapp_message_count = fields.Integer(compute="_compute_whatsapp_message_count")

    def _whatsapp_phone(self):
        self.ensure_one()
        return (getattr(self, "mobile", False) or self.phone
                or self.partner_id.phone or getattr(self.partner_id, "mobile", False) or "")

    def _whatsapp_conversation_phones(self):
        self.ensure_one()
        provider = self.env["maisfibra.whatsapp.provider"]
        raw_phone = self._whatsapp_phone()
        norm_phone = provider.normalize_phone(raw_phone)
        phones = set()
        if raw_phone:
            digits = re.sub(r"\D", "", raw_phone)
            if digits:
                phones.add(digits)
        if norm_phone:
            phones.add(norm_phone)
            phones.update(provider.get_phone_aliases(norm_phone))
        return raw_phone, norm_phone, phones

    def _whatsapp_conversation_domain(self):
        self.ensure_one()
        _raw, _norm, phones = self._whatsapp_conversation_phones()
        clauses = [("lead_id", "=", self.id)]
        if self.partner_id:
            clauses.append(("partner_id", "=", self.partner_id.id))
        if phones:
            clauses.append(("phone", "in", list(phones)))
        return ["|"] * (len(clauses) - 1) + clauses

    def _compute_whatsapp_message_count(self):
        Msg = self.env["maisfibra.whatsapp.message"].sudo()
        for lead in self:
            lead.whatsapp_message_count = Msg.search_count(lead._whatsapp_conversation_domain())

    def _wa_finalize_outbound(self, wa_msg, text_for_chatter, attachment=None):
        """Posta a mensagem enviada no Chatter, sincroniza o Discuss, pausa o bot e notifica o bus."""
        Message = self.env["maisfibra.whatsapp.message"]
        Message._post_on_record(self, "out", text_for_chatter, self.env.user.name)
        if self.partner_id:
            try:
                channel = self.env["discuss.channel"].sudo().with_user(self.env.user)._get_or_create_chat(
                    partners_to=[self.partner_id.id], pin=True
                )
                post_kwargs = {
                    "body": text_for_chatter,
                    "author_id": self.env.user.partner_id.id,
                    "message_type": "comment",
                    "subtype_xmlid": "mail.mt_comment",
                }
                if attachment:
                    post_kwargs["attachment_ids"] = [attachment.id]
                channel.sudo().with_context(wa_skip_outbound=True).message_post(**post_kwargs)
            except Exception:
                pass
        self._wa_pausar_bot()
        Message._notify_chat_bus(wa_msg)

    def _wa_dispatch_payload(self, norm_phone, clean_body, media_base64=False, media_mime=False, media_filename=False):
        provider = self.env["maisfibra.whatsapp.provider"]
        if media_base64:
            mime = media_mime or "application/octet-stream"
            return provider.send_media(
                norm_phone,
                media_base64,
                mime,
                media_filename or "anexo",
                caption=clean_body,
                is_voice=mime.startswith("audio/"),
                user_id=self.env.user.id,
            )
        return provider.send_text(norm_phone, clean_body, user_id=self.env.user.id)

    def get_whatsapp_chat_history(self, limit=200):
        self.ensure_one()
        self.check_access("read")
        provider = self.env["maisfibra.whatsapp.provider"]
        Message = self.env["maisfibra.whatsapp.message"].sudo()
        raw_phone, norm_phone, phones = self._whatsapp_conversation_phones()
        msgs = Message.search(self._whatsapp_conversation_domain(), order="create_date desc, id desc", limit=limit)[::-1]
        provider_name = provider._param("provider", "chromium")
        if provider_name == "chromium":
            session = provider.get_user_session_status(self.env.user.id, timeout=1.5)
            session_status = session.get("status", "disconnected")
        else:
            session_status = "configured"
        return {
            "lead_id": self.id,
            "lead_name": self.name or "",
            "contact_name": (self.partner_id.name if self.partner_id else False) or self.contact_name or self.name or "",
            "phone": raw_phone,
            "normalized_phone": norm_phone,
            "phone_aliases": sorted(phones),
            "partner_id": self.partner_id.id or False,
            "can_send": bool(norm_phone),
            "provider": provider_name,
            "session_status": session_status,
            "messages": Message._serialize_chat_messages(msgs),
        }

    def action_send_whatsapp_chat_message(self, body="", media_base64=False, media_mime=False, media_filename=False):
        self.ensure_one()
        self.check_access("write")
        provider = self.env["maisfibra.whatsapp.provider"]
        Message = self.env["maisfibra.whatsapp.message"].sudo()
        norm_phone = provider.normalize_phone(self._whatsapp_phone())
        if not norm_phone:
            raise UserError(_("Este lead não possui telefone cadastrado para envio de WhatsApp."))

        clean_body = (body or "").strip()
        if not clean_body and not media_base64:
            raise UserError(_("Digite uma mensagem ou anexe um arquivo antes de enviar."))

        attachment = False
        media_type = "text"
        mime = media_mime or ""
        filename = media_filename or ""
        b64_payload = media_base64
        if b64_payload:
            if isinstance(b64_payload, str) and b64_payload.startswith("data:") and ";base64," in b64_payload:
                b64_payload = b64_payload.split(";base64,", 1)[1]
            mime = mime or "application/octet-stream"
            filename = filename or "anexo"
            media_type = Message._normalize_media_type(media_mime=mime, media_url=filename)
            attachment = self.env["ir.attachment"].sudo().create({
                "name": filename,
                "datas": b64_payload,
                "mimetype": mime,
                "res_model": "crm.lead",
                "res_id": self.id,
            })

        text_body = clean_body or f"[{media_type}]"
        msg_vals = {
            "lead_id": self.id,
            "partner_id": self.partner_id.id or False,
            "phone": norm_phone,
            "direction": "out",
            "body": text_body,
            "user_id": self.env.user.id,
            "origem": "manual",
            "media_type": media_type,
            "media_mime": mime or False,
            "media_filename": filename or False,
            "attachment_id": attachment.id if attachment else False,
        }
        try:
            external_id = self._wa_dispatch_payload(
                norm_phone,
                clean_body,
                media_base64=b64_payload,
                media_mime=mime,
                media_filename=filename,
            )
        except Exception as err:  # noqa: BLE001 — registra falha no histórico sem quebrar a UI
            msg_vals.update({"state": "error", "error_message": str(err)[:500]})
            wa_msg = Message.create(msg_vals)
            Message._notify_chat_bus(wa_msg)
            return {
                "ok": False,
                "error": str(err),
                "message": Message._serialize_chat_messages(wa_msg)[0],
            }

        msg_vals.update({"state": "sent", "external_id": external_id or False})
        wa_msg = Message.create(msg_vals)
        self._wa_finalize_outbound(wa_msg, text_body, attachment=attachment)
        return {
            "ok": True,
            "message": Message._serialize_chat_messages(wa_msg)[0],
        }

    def action_retry_whatsapp_message(self, message_id):
        self.ensure_one()
        self.check_access("write")
        provider = self.env["maisfibra.whatsapp.provider"]
        Message = self.env["maisfibra.whatsapp.message"].sudo()
        msg = Message.search([("id", "=", int(message_id))] + self._whatsapp_conversation_domain(), limit=1)
        if not msg or msg.direction != "out" or msg.state != "error":
            raise UserError(_("Mensagem inválida ou não disponível para reenvio neste lead."))

        norm_phone = provider.normalize_phone(msg.phone or self._whatsapp_phone())
        if not norm_phone:
            raise UserError(_("Este lead não possui telefone cadastrado para reenvio de WhatsApp."))

        attachment = msg.attachment_id
        b64_payload = False
        mime = False
        filename = False
        clean_body = msg.body or ""
        if attachment:
            b64_payload = attachment.datas.decode() if isinstance(attachment.datas, bytes) else attachment.datas
            mime = msg.media_mime or attachment.mimetype or "application/octet-stream"
            filename = msg.media_filename or attachment.name or "anexo"
            if clean_body in ("[image]", "[audio]", "[voice]", "[video]", "[document]", "[sticker]"):
                clean_body = ""

        try:
            external_id = self._wa_dispatch_payload(
                norm_phone,
                clean_body,
                media_base64=b64_payload,
                media_mime=mime,
                media_filename=filename,
            )
        except Exception as err:  # noqa: BLE001
            msg.write({"error_message": str(err)[:500]})
            Message._notify_chat_bus(msg)
            return {
                "ok": False,
                "error": str(err),
                "message": Message._serialize_chat_messages(msg)[0],
            }

        msg.write({
            "state": "sent",
            "external_id": external_id or False,
            "error_message": False,
            "lead_id": msg.lead_id.id or self.id,
        })
        self._wa_finalize_outbound(msg, msg.body or f"[{msg.media_type}]", attachment=attachment)
        return {
            "ok": True,
            "message": Message._serialize_chat_messages(msg)[0],
        }

    def action_open_whatsapp(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Enviar WhatsApp",
            "res_model": "maisfibra.whatsapp.composer",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_res_model": self._name,
                "default_res_id": self.id,
                "default_phone": self._whatsapp_phone(),
            },
        }

    def action_view_whatsapp_messages(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "WhatsApp",
            "res_model": "maisfibra.whatsapp.message",
            "view_mode": "list,form",
            "domain": self._whatsapp_conversation_domain(),
            "context": {"create": False},
        }
