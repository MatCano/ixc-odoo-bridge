from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    mf_bridge_url = fields.Char("URL do bridge", config_parameter="maisfibra_crm.bridge_url")
    mf_bridge_token = fields.Char("Token do bridge", config_parameter="maisfibra_crm.bridge_token")
