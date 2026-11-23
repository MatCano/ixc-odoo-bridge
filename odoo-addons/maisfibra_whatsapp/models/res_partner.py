from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    mf_whatsapp_sync = fields.Boolean("Sincronizado do WhatsApp", default=False, index=True, copy=False)
    whatsapp_message_ids = fields.One2many("maisfibra.whatsapp.message", "partner_id", string="Mensagens WhatsApp")
    whatsapp_message_count = fields.Integer(compute="_compute_whatsapp_message_count")

    @api.model
    def _mf_cleanup_whatsapp_synced_contacts(self):
        """Arquiva contatos criados automaticamente pela sincronização do WhatsApp sem apagar referências críticas."""
        Partner = self.with_context(active_test=False).sudo()
        synced = Partner.search([
            "|",
            ("comment", "ilike", "Contato de WhatsApp ("),
            ("comment", "ilike", "Contato sincronizado do WhatsApp ("),
        ])
        if not synced:
            return 0
        protected_ids = set(
            self.env["res.users"].with_context(active_test=False).sudo().search([
                ("partner_id", "in", synced.ids)
            ]).mapped("partner_id").ids
        )
        if "account.move" in self.env:
            protected_ids.update(
                self.env["account.move"].sudo().search([
                    ("partner_id", "in", synced.ids)
                ]).mapped("partner_id").ids
            )
        if "maisfibra.cobranca" in self.env:
            protected_ids.update(
                self.env["maisfibra.cobranca"].sudo().search([
                    ("partner_id", "in", synced.ids)
                ]).mapped("partner_id").ids
            )
        to_archive = synced.filtered(
            lambda p: p.id not in protected_ids and (p.active or not p.mf_whatsapp_sync)
        )
        if to_archive:
            to_archive.write({"mf_whatsapp_sync": True, "active": False})
        return len(to_archive)

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
