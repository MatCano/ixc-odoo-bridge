from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    whatsapp_message_ids = fields.One2many("maisfibra.whatsapp.message", "partner_id", string="Mensagens WhatsApp")
    whatsapp_message_count = fields.Integer(compute="_compute_whatsapp_message_count")

    def _compute_whatsapp_message_count(self):
        for partner in self:
            partner.whatsapp_message_count = len(partner.whatsapp_message_ids)

    def _whatsapp_phone(self):
        self.ensure_one()
        return getattr(self, "mobile", False) or self.phone or ""

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
            "domain": [("partner_id", "=", self.id)],
            "context": {"create": False},
        }
