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
        [("sent", "Enviada"), ("received", "Recebida"), ("error", "Erro")],
        string="Status", default="sent",
    )
    external_id = fields.Char("ID externo", index=True, copy=False)
    error_message = fields.Text("Erro")
    user_id = fields.Many2one("res.users", string="Enviada por")

    _sql_constraints = [
        ("external_id_uniq", "unique(external_id)", "Mensagem já registrada."),
    ]

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
        """Procura contato pelos últimos 8 dígitos e confirma pelo número normalizado."""
        provider = self.env["maisfibra.whatsapp.provider"]
        Partner = self.env["res.partner"].sudo()
        tail = number[-8:]
        phone_fields = [f for f in ("phone", "mobile") if f in Partner._fields]
        domain = []
        for f in phone_fields:
            domain = (["|"] + domain if domain else []) + [(f, "ilike", tail)]
        for partner in Partner.search(domain, limit=20):
            for f in phone_fields:
                if provider.normalize_phone(partner[f]) == number:
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
        ICP = self.env["ir.config_parameter"].sudo()
        team_id = int(ICP.get_param("maisfibra_whatsapp.default_team_id") or 0)
        source = self.env.ref("maisfibra_whatsapp.utm_source_whatsapp", raise_if_not_found=False)
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
        lead = Lead.create(vals)
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
