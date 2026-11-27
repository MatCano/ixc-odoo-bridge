{
    "name": "MaisFibra - Automação de CRM",
    "version": "19.0.1.0.0",
    "summary": "Motor visual de automação comercial e de relacionamento CRM (gatilhos, segmentação, condições, esperas, multicanal e auditoria)",
    "description": """
Motor de Automação de CRM para Odoo 19:
- Construtor visual de fluxos sem programação (OWL 2) com categorias, conexões, validação e prévia
- Gatilhos orientados a eventos de CRM, atividades, datas, inatividade, segmentos e webhooks externos
- Segmentação declarativa segura com grupos AND/OR aninhados e 14 operadores
- Ações de CRM, movimentação entre pipelines, WhatsApp, E-mail e Webhooks com variáveis dinâmicas
- Esperas persistentes no banco (tempo, janela horária, condição, resposta, atividade, etapa e timeout)
- Políticas configuráveis de entrada, reentrada, permanência e saída
- Versionamento imutável na publicação, monitoramento em tempo real, retentativas e auditoria sanitizada
    """,
    "author": "MaisFibra / Allnnect",
    "category": "Sales/CRM",
    "license": "LGPL-3",
    "depends": ["base", "mail", "crm", "contacts", "web", "maisfibra_crm", "maisfibra_whatsapp"],
    "data": [
        "security/groups.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence.xml",
        "data/ir_cron.xml",
        "views/automation_views.xml",
        "views/segment_views.xml",
        "views/execution_views.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "maisfibra_automacao/static/src/components/automation_builder/*.js",
            "maisfibra_automacao/static/src/components/automation_builder/*.xml",
            "maisfibra_automacao/static/src/components/automation_builder/*.scss",
            "maisfibra_automacao/static/src/components/automation_monitor/*.js",
            "maisfibra_automacao/static/src/components/automation_monitor/*.xml",
            "maisfibra_automacao/static/src/components/automation_monitor/*.scss",
        ],
    },
    "installable": True,
    "application": True,
}
