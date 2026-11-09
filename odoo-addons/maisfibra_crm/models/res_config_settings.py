from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    mf_bridge_url = fields.Char("URL do bridge", config_parameter="maisfibra_crm.bridge_url")
    mf_bridge_token = fields.Char("Token do bridge", config_parameter="maisfibra_crm.bridge_token")
    mf_email_boas_vindas = fields.Boolean("E-mail de boas-vindas para contato novo",
                                          config_parameter="maisfibra_crm.email_boas_vindas")
    mf_email_estagio = fields.Boolean("E-mail de confirmação ao mudar de estágio",
                                      config_parameter="maisfibra_crm.email_estagio")

    def action_mf_migrar(self):
        return self.env["maisfibra.migracao"].action_migrar()
