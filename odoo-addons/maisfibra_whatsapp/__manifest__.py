{
    "name": "MaisFibra - WhatsApp",
    "version": "19.0.1.0.0",
    "summary": "WhatsApp dentro do CRM (Evolution API, trocável para Meta Cloud API)",
    "description": """
WhatsApp integrado ao Odoo para a MaisFibra:
- Botão de WhatsApp no Lead/Oportunidade e no Contato
- Envio de mensagens pelo provedor configurado (Evolution API; Meta preparado)
- Recebimento via webhook: mensagem vai pro chatter; número novo cria lead com origem WhatsApp
- Histórico de mensagens em CRM > WhatsApp
    """,
    "author": "MaisFibra",
    "category": "Sales/CRM",
    "license": "LGPL-3",
    "depends": ["base", "mail", "crm", "utm"],
    "data": [
        "security/ir.model.access.csv",
        "wizard/whatsapp_composer_views.xml",
        "views/whatsapp_message_views.xml",
        "views/crm_lead_views.xml",
        "views/res_partner_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "external_dependencies": {"python": ["requests"]},
    "installable": True,
    "application": False,
}
