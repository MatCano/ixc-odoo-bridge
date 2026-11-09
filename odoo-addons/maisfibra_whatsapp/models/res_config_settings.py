from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    wa_provider = fields.Selection(
        [("evolution", "Evolution API"), ("meta", "Meta Cloud API (oficial)")],
        string="Provedor", default="evolution",
        config_parameter="maisfibra_whatsapp.provider",
    )
    wa_evolution_url = fields.Char("URL da Evolution", config_parameter="maisfibra_whatsapp.evolution_url")
    wa_evolution_apikey = fields.Char("API Key da Evolution", config_parameter="maisfibra_whatsapp.evolution_apikey")
    wa_evolution_instance = fields.Char("Instância", config_parameter="maisfibra_whatsapp.evolution_instance")
    wa_webhook_token = fields.Char("Token do webhook", config_parameter="maisfibra_whatsapp.webhook_token")
    wa_default_team_id = fields.Many2one(
        "crm.team", string="Funil para leads novos do WhatsApp",
        config_parameter="maisfibra_whatsapp.default_team_id",
    )
