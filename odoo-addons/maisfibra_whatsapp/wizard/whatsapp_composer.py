from odoo import _, fields, models
from odoo.exceptions import UserError


class WhatsappComposer(models.TransientModel):
    _name = "maisfibra.whatsapp.composer"
    _description = "Enviar WhatsApp"

    res_model = fields.Char(required=True)
    res_id = fields.Integer(required=True)
    phone = fields.Char("Telefone", required=True)
    body = fields.Text("Mensagem", required=True)

    def action_send(self):
        self.ensure_one()
        if self.res_model not in ("crm.lead", "res.partner"):
            raise UserError(_("Modelo não suportado."))
        record = self.env[self.res_model].browse(self.res_id).exists()
        if not record:
            raise UserError(_("Registro não encontrado."))

        provider = self.env["maisfibra.whatsapp.provider"]
        number = provider.normalize_phone(self.phone)
        external_id = provider.send_text(number, self.body)  # levanta UserError se falhar

        is_lead = self.res_model == "crm.lead"
        self.env["maisfibra.whatsapp.message"].sudo().create({
            "lead_id": record.id if is_lead else False,
            "partner_id": (record.partner_id.id if is_lead else record.id) or False,
            "phone": number,
            "direction": "out",
            "body": self.body,
            "state": "sent",
            "external_id": external_id or False,
            "user_id": self.env.user.id,
        })
        self.env["maisfibra.whatsapp.message"]._post_on_record(
            record, "out", self.body, self.env.user.name
        )
        if is_lead:
            record._wa_pausar_bot()  # atendimento humano em andamento
        return {"type": "ir.actions.act_window_close"}
