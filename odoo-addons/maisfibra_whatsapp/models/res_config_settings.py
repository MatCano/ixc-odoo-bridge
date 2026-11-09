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

    wa_auto_boas_vindas = fields.Boolean("Boas-vindas automática para lead novo",
                                         config_parameter="maisfibra_whatsapp.auto_boas_vindas")
    wa_msg_boas_vindas = fields.Char(
        "Mensagem de boas-vindas", config_parameter="maisfibra_whatsapp.msg_boas_vindas",
        default="Olá, {nome}! Obrigado pelo contato com a Mais Fibra. Em breve {vendedor} vai falar com você. 🚀",
    )
    wa_auto_estagio = fields.Boolean("Mensagem ao mudar de estágio",
                                     config_parameter="maisfibra_whatsapp.auto_estagio")
    wa_bot_ativo = fields.Boolean("Chatbot ativo", config_parameter="maisfibra_whatsapp.bot_ativo")
    wa_bot_menu = fields.Char("Menu do chatbot (vazio = padrão)", config_parameter="maisfibra_whatsapp.bot_menu")
    wa_bot_pausa_horas = fields.Integer("Pausar o bot após atendimento humano (horas)", default=12,
                                        config_parameter="maisfibra_whatsapp.bot_pausa_horas")
