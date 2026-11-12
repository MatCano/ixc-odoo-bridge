import logging

from markupsafe import Markup, escape

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


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

    _external_id_uniq = models.Constraint("unique(external_id)", "Mensagem já registrada.")

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

        for partner in Partner.search(domain, limit=50):
            for f in phone_fields:
                val = partner[f]
                if val and provider.normalize_phone(val) == norm_number:
                    return partner
        return Partner

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
    def process_inbound(self, number, text, external_id="", push_name="", from_me=False):
        """Registra a mensagem e posta no chatter do lead/contato."""
        if external_id and self.sudo().search_count([("external_id", "=", external_id)]):
            return  # já registrada (ex.: mensagem que nós mesmos enviamos pelo Odoo)
        partner = self._find_partner(number)
        lead = False
        if not from_me:
            lead = self._find_or_create_lead(partner, number, push_name)
        direction = "out" if from_me else "in"
        self.sudo().create({
            "partner_id": partner.id or False,
            "lead_id": lead.id if lead else False,
            "phone": number,
            "direction": direction,
            "body": text,
            "state": "sent" if from_me else "received",
            "external_id": external_id or False,
        })
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
                self._post_on_record(msg.lead_id or msg.partner_id, "out", msg.body,
                                     "Chatbot" if msg.origem == "bot" else "Automação")
            except Exception as e:  # noqa: BLE001 — falha de um envio não pode travar a fila
                _logger.warning("WhatsApp: falha ao enviar %s: %s", msg.id, e)
                msg.write({"state": "error", "error_message": str(e)[:500]})
            self.env.cr.commit()  # cada envio é independente
