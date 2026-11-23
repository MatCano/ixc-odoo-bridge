{
    "name": "MaisFibra - WhatsApp",
    "version": "19.0.1.1.0",
    "summary": "WhatsApp dentro do CRM (Evolution API, trocável para Meta Cloud API)",
    "description": """
WhatsApp integrado ao Odoo para a MaisFibra:
- Botão de WhatsApp no Lead/Oportunidade e no Contato
- Envio de mensagens pelo provedor configurado (Evolution API; Meta preparado)
- Recebimento via webhook: mensagem vai pro chatter; número novo cria lead com origem WhatsApp
- Automações: boas-vindas, mensagem por estágio e chatbot básico (desligados por padrão)\n- Histórico de mensagens em CRM > WhatsApp
    """,
    "author": "MaisFibra",
    "category": "Sales/CRM",
    "license": "LGPL-3",
    "depends": ["base", "contacts", "mail", "crm", "utm", "web"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "wizard/whatsapp_composer_views.xml",
        "views/whatsapp_message_views.xml",
        "views/crm_lead_views.xml",
        "views/res_partner_views.xml",
        "views/res_config_settings_views.xml",
        "views/crm_stage_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "maisfibra_whatsapp/static/src/components/lead_whatsapp_chat/lead_whatsapp_chat.js",
            "maisfibra_whatsapp/static/src/components/lead_whatsapp_chat/chatter_whatsapp_patch.js",
            "maisfibra_whatsapp/static/src/components/lead_whatsapp_chat/lead_whatsapp_chat.xml",
            "maisfibra_whatsapp/static/src/components/lead_whatsapp_chat/lead_whatsapp_chat.scss",
        ],
    },
    "external_dependencies": {"python": ["requests"]},
    "installable": True,
    "application": False,
}
