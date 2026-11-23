import logging
import mimetypes
from datetime import timezone

from markupsafe import Markup, escape

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

VALID_MEDIA_TYPES = {"text", "image", "audio", "voice", "video", "document", "sticker"}
EXTRA_MIME_BY_EXT = {
    ".webp": "image/webp",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".oga": "audio/ogg",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
}


class WhatsappMessage(models.Model):
    _name = "maisfibra.whatsapp.message"
    _description = "Mensagem de WhatsApp"
    _order = "create_date desc, id desc"
    _rec_name = "phone"

    partner_id = fields.Many2one("res.partner", string="Contato", index=True, ondelete="set null")
    lead_id = fields.Many2one("crm.lead", string="Lead/Oportunidade", index=True, ondelete="set null")
    phone = fields.Char("Telefone", index=True, required=True)
    direction = fields.Selection(
        [("in", "Recebida"), ("out", "Enviada")], string="Direção", required=True
    )
    body = fields.Text("Mensagem")
    state = fields.Selection(
        [("queued", "Na fila"), ("sent", "Enviada"), ("received", "Recebida"), ("error", "Erro")],
        string="Status", default="sent",
    )
    external_id = fields.Char("ID externo", index=True, copy=False)
    error_message = fields.Text("Erro")
    user_id = fields.Many2one("res.users", string="Enviada por")
    origem = fields.Selection(
        [("manual", "Manual"), ("auto", "Automação"), ("bot", "Chatbot")],
        string="Origem", default="manual",
    )
    media_type = fields.Selection(
        [
            ("text", "Texto"),
            ("image", "Imagem"),
            ("audio", "Áudio"),
            ("voice", "Voz"),
            ("video", "Vídeo"),
            ("document", "Documento"),
            ("sticker", "Figurinha"),
        ],
        string="Tipo de mídia",
        default="text",
    )
    media_url = fields.Char("URL da mídia")
    media_mime = fields.Char("MIME da mídia")
    media_filename = fields.Char("Arquivo de mídia")
    attachment_id = fields.Many2one("ir.attachment", string="Anexo", ondelete="set null")

    _external_id_uniq = models.Constraint("unique(external_id)", "Mensagem já registrada.")

    @api.model
    def _normalize_media_type(self, media_type=None, media_mime="", media_url=""):
        kind = (media_type or "").strip().lower()
        if kind == "ptt":
            kind = "voice"
        elif kind == "chat":
            kind = "text"
        if kind in VALID_MEDIA_TYPES and (kind != "text" or not media_url):
            return kind
        mime = (media_mime or "").lower()
        if mime.startswith("image/"):
            return "image"
        if mime.startswith("audio/"):
            return "voice" if kind == "voice" else "audio"
        if mime.startswith("video/"):
            return "video"
        if media_url:
            return "document"
        return kind if kind in VALID_MEDIA_TYPES else "text"

    @api.model
    def _guess_mime(self, filename_or_url):
        if not filename_or_url:
            return ""
        lower = filename_or_url.split("?")[0].lower()
        for ext, mime in EXTRA_MIME_BY_EXT.items():
            if lower.endswith(ext):
                return mime
        guessed, _ = mimetypes.guess_type(lower)
        return guessed or ""

    # ------------------------------------------------------------------
    # Chatter
    # ------------------------------------------------------------------
    @api.model
    def _post_on_record(self, record, direction, body, author_name=""):
        if not record:
            return
        icon = "📥" if direction == "in" else "📤"
        label = "WhatsApp recebido" if direction == "in" else "WhatsApp enviado"
        who = f" · {escape(author_name)}" if author_name else ""
        html = Markup("<p><b>{} {}</b>{}</p><p>{}</p>").format(
            icon, label, Markup(who), escape(body or "").replace("\n", Markup("<br/>"))
        )
        record.sudo().message_post(
            body=html, message_type="comment", subtype_xmlid="mail.mt_note"
        )

    # ------------------------------------------------------------------
    # Entrada (chamado pelo webhook)
    # ------------------------------------------------------------------
    @api.model
    def _find_partner(self, number):
        """Procura contato pelos últimos dígitos e confirma pelo número normalizado."""
        provider = self.env["maisfibra.whatsapp.provider"]
        Partner = self.env["res.partner"].sudo()
        norm_number = provider.normalize_phone(number)
        if not norm_number:
            return Partner

        # Usa os últimos 4 dígitos para busca inicial resiliente a formatações (+55 11 99123-0067 vs 5511991230067)
        tail = norm_number[-4:] if len(norm_number) >= 4 else norm_number
        phone_fields = [f for f in ("phone", "mobile") if f in Partner._fields]
        domain = []
        for f in phone_fields:
            domain = (["|"] + domain if domain else []) + [(f, "ilike", tail)]
        if domain:
            domain = [("mf_whatsapp_sync", "=", False)] + domain

        for partner in Partner.search(domain, limit=50):
            for f in phone_fields:
                val = partner[f]
                if val and provider.normalize_phone(val) == norm_number:
                    return partner
        return Partner

    @api.model
    def _find_existing_lead(self, partner, number):
        """Localiza lead existente sem criar novo registro (usado em mensagens enviadas / espelhadas)."""
        Lead = self.env["crm.lead"].sudo()
        provider = self.env["maisfibra.whatsapp.provider"]
        norm = provider.normalize_phone(number)
        if partner:
            lead = Lead.search(
                [("partner_id", "=", partner.id), ("stage_id.is_won", "=", False)],
                order="create_date desc, id desc", limit=1,
            )
            if lead:
                return lead
        if norm:
            tail = norm[-8:] if len(norm) >= 8 else norm
            for lead in Lead.search([("phone", "ilike", tail), ("stage_id.is_won", "=", False)],
                                    order="create_date desc, id desc", limit=10):
                if provider.normalize_phone(lead.phone) == norm:
                    return lead
        if partner:
            lead = Lead.search([("partner_id", "=", partner.id)], order="create_date desc, id desc", limit=1)
            if lead:
                return lead
        if norm:
            tail = norm[-8:] if len(norm) >= 8 else norm
            for lead in Lead.search([("phone", "ilike", tail)], order="create_date desc, id desc", limit=10):
                if provider.normalize_phone(lead.phone) == norm:
                    return lead
        return Lead

    @api.model
    def _find_or_create_lead(self, partner, number, push_name):
        Lead = self.env["crm.lead"].sudo()
        if partner:
            lead = Lead.search(
                [("partner_id", "=", partner.id), ("stage_id.is_won", "=", False)],
                order="create_date desc", limit=1,
            )
            if lead:
                return lead
        # Sem contato cadastrado: procura lead aberto pelo telefone, senão cada mensagem
        # de um número desconhecido criaria um lead novo.
        provider = self.env["maisfibra.whatsapp.provider"]
        for lead in Lead.search([("phone", "ilike", number[-8:]), ("stage_id.is_won", "=", False)],
                                order="create_date desc", limit=10):
            if provider.normalize_phone(lead.phone) == number:
                return lead
        ICP = self.env["ir.config_parameter"].sudo()
        team_id = int(ICP.get_param("maisfibra_whatsapp.default_team_id") or 0)
        # reaproveita a origem "WhatsApp" que já existe no Odoo da MaisFibra
        Source = self.env["utm.source"].sudo()
        source = Source.search([("name", "=ilike", "whatsapp")], limit=1) or Source.create({"name": "WhatsApp"})
        name = (partner.name if partner else "") or push_name or number
        vals = {
            "name": f"[WhatsApp] {name}",
            "contact_name": push_name or False,
            "partner_id": partner.id if partner else False,
            "phone": number,
            "type": "opportunity",
            "source_id": source.id if source else False,
        }
        if team_id:
            vals["team_id"] = team_id
        lead = Lead.with_context(wa_sem_boas_vindas=True).create(vals)
        _logger.info("WhatsApp: lead %s criado para %s", lead.id, number)
        return lead

    @api.model
    def _notify_chat_bus(self, msg):
        group_user = self.env.ref("base.group_user", raise_if_not_found=False)
        if group_user and msg:
            self.env["bus.bus"].sudo()._sendone(
                group_user,
                "maisfibra_whatsapp/message",
                {
                    "message_id": msg.id,
                    "lead_id": msg.lead_id.id or False,
                    "partner_id": msg.partner_id.id or False,
                    "phone": msg.phone or "",
                    "direction": msg.direction,
                    "state": msg.state,
                },
            )

    @api.model
    def _lookup_sqlite_media(self, external_ids):
        ids = [eid for eid in (external_ids or []) if eid]
        if not ids:
            return {}
        conn = self.env["maisfibra.whatsapp.provider"]._get_sqlite_conn()
        if not conn:
            return {}
        result = {}
        try:
            placeholders = ",".join("?" for _ in ids)
            cur = conn.cursor()
            cur.execute(
                f"SELECT id, type, media_url, media_mime, media_filename "
                f"FROM whatsapp_messages WHERE id IN ({placeholders})",
                tuple(ids),
            )
            for row in cur.fetchall():
                result[row[0]] = {
                    "type": row[1] or "",
                    "media_url": row[2] or "",
                    "media_mime": row[3] or "",
                    "media_filename": row[4] or "",
                }
        except Exception as exc:
            _logger.debug("WhatsApp SQLite media lookup falhou: %s", exc)
        finally:
            conn.close()
        return result

    @api.model
    def _serialize_chat_messages(self, messages):
        need_lookup = [
            m.external_id for m in messages
            if m.external_id and not m.attachment_id and not m.media_url
        ]
        sqlite_map = self._lookup_sqlite_media(need_lookup)
        serialized = []
        for m in messages:
            sq = sqlite_map.get(m.external_id, {}) if m.external_id else {}
            if m.attachment_id:
                media_url = f"/web/content/{m.attachment_id.id}"
                media_mime = m.media_mime or m.attachment_id.mimetype or ""
                media_filename = m.media_filename or m.attachment_id.name or ""
            else:
                media_url = m.media_url or sq.get("media_url") or ""
                media_mime = m.media_mime or sq.get("media_mime") or ""
                media_filename = m.media_filename or sq.get("media_filename") or ""
            if media_url and not media_mime:
                media_mime = self._guess_mime(media_filename or media_url)
            media_type = self._normalize_media_type(
                m.media_type if m.media_type and m.media_type != "text" else sq.get("type") or m.media_type,
                media_mime=media_mime,
                media_url=media_url,
            )
            if m.create_date:
                dt_utc = m.create_date.replace(tzinfo=timezone.utc)
                create_date_iso = dt_utc.isoformat()
                timestamp_ms = int(dt_utc.timestamp() * 1000)
            else:
                create_date_iso = False
                timestamp_ms = 0
            if m.direction == "out":
                if m.user_id:
                    author_name = m.user_id.name
                elif m.origem == "bot":
                    author_name = "Chatbot"
                elif m.origem == "auto":
                    author_name = "Automação"
                else:
                    author_name = "Equipe"
            else:
                author_name = (m.partner_id.name if m.partner_id else "") or (m.lead_id.contact_name if m.lead_id else "") or m.phone or ""
            serialized.append({
                "id": m.id,
                "direction": m.direction,
                "body": m.body or "",
                "state": m.state,
                "error_message": m.error_message or "",
                "origem": m.origem or "manual",
                "author_name": author_name,
                "create_date": create_date_iso,
                "timestamp_ms": timestamp_ms,
                "media_type": media_type,
                "media_url": media_url or False,
                "media_mime": media_mime or False,
                "media_filename": media_filename or False,
                "external_id": m.external_id or False,
            })
        return serialized

    @api.model
    def process_inbound(
        self,
        number,
        text,
        external_id="",
        push_name="",
        from_me=False,
        media_type="text",
        media_url=False,
        media_mime=False,
        media_filename=False,
        attachment_id=False,
    ):
        """Registra a mensagem e posta no chatter do lead/contato."""
        if external_id and self.sudo().search_count([("external_id", "=", external_id)]):
            return  # já registrada (ex.: mensagem que nós mesmos enviamos pelo Odoo)
        partner = self._find_partner(number)
        if from_me:
            lead = self._find_existing_lead(partner, number)
        else:
            lead = self._find_or_create_lead(partner, number, push_name)
        direction = "out" if from_me else "in"
        norm_media_type = self._normalize_media_type(media_type, media_mime=media_mime or "", media_url=media_url or "")
        msg = self.sudo().create({
            "partner_id": partner.id or False,
            "lead_id": lead.id if lead else False,
            "phone": number,
            "direction": direction,
            "body": text,
            "state": "sent" if from_me else "received",
            "external_id": external_id or False,
            "media_type": norm_media_type,
            "media_url": media_url or False,
            "media_mime": media_mime or False,
            "media_filename": media_filename or False,
            "attachment_id": attachment_id or False,
        })
        self._notify_chat_bus(msg)
        target = lead or partner
        self._post_on_record(target, direction, text, push_name if not from_me else "")
        if from_me and lead:
            lead.sudo()._wa_pausar_bot()  # alguém da equipe respondeu pelo celular
        elif lead:
            self.env["maisfibra.whatsapp.bot"].sudo().responder(lead, number, text)

    # ------------------------------------------------------------------
    # Fila de envio (automações e chatbot não travam a tela do usuário)
    # ------------------------------------------------------------------
    @api.model
    def enfileirar(self, phone, body, lead=None, partner=None, origem="auto"):
        provider = self.env["maisfibra.whatsapp.provider"]
        number = provider.normalize_phone(phone)
        if not number or not (body or "").strip():
            return self.browse()
        msg = self.sudo().create({
            "phone": number, "body": body, "direction": "out", "state": "queued", "origem": origem,
            "lead_id": lead.id if lead else False,
            "partner_id": (partner or (lead.partner_id if lead else False) or self.env["res.partner"]).id or False,
        })
        cron = self.env.ref("maisfibra_whatsapp.cron_enviar_fila", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()
        return msg

    @api.model
    def _cron_enviar_fila(self, limite=50):
        provider = self.env["maisfibra.whatsapp.provider"]
        for msg in self.sudo().search([("state", "=", "queued")], order="id", limit=limite):
            try:
                ext = provider.send_text(msg.phone, msg.body)
                msg.write({"state": "sent", "external_id": ext or False})
                self._notify_chat_bus(msg)
                self._post_on_record(msg.lead_id or msg.partner_id, "out", msg.body,
                                     "Chatbot" if msg.origem == "bot" else "Automação")
            except Exception as e:  # noqa: BLE001 — falha de um envio não pode travar a fila
                _logger.warning("WhatsApp: falha ao enviar %s: %s", msg.id, e)
                msg.write({"state": "error", "error_message": str(e)[:500]})
                self._notify_chat_bus(msg)
            self.env.cr.commit()  # cada envio é independente
