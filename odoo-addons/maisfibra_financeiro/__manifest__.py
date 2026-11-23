{
    "name": "MaisFibra - Financeiro",
    "version": "19.0.2.0.0",
    "summary": "Boletos, Cobranças Recorrentes, Notas Fiscais, Aprovação Multinível, Agenda, Fluxo de Caixa e DRE",
    "description": """
Módulo Financeiro Completo MaisFibra (Frente 3 + Boletos & Notas Fiscais):
- Gestão de contratos recorrentes, pró-rata, descontos, multa/juros e 2ª via
- Emissão e sincronização de boletos (Modos A: Odoo FEBRABAN, B: IXC, C: Híbrido)
- Geração de PDF bancário + código de barras I2of5 + QR Code PIX + Portal Público
- Envio automático/manual auditado por E-mail e WhatsApp
- Emissão e integração de Notas Fiscais (NFCom / NFS-e) com XML/PDF e controle de rejeições
- Fila de exceções, logs sanitizados e Painel Financeiro OWL
- Aprovação multinível de contas a pagar, agenda de pagamentos, fluxo de caixa previsto e DRE
    """,
    "author": "MaisFibra / Allnnect",
    "category": "Accounting",
    "license": "LGPL-3",
    "depends": ["account", "analytic", "mail", "web", "maisfibra_crm"],
    "data": [
        "security/groups.xml",
        "security/ir.model.access.csv",
        "data/analytic_data.xml",
        "data/ir_sequence.xml",
        "data/ir_cron.xml",
        "data/mail_templates.xml",
        "wizard/cobranca_wizards_views.xml",
        "views/aprovacao_views.xml",
        "views/relatorios_views.xml",
        "views/contrato_views.xml",
        "views/cobranca_views.xml",
        "views/nota_fiscal_views.xml",
        "views/cobranca_envio_views.xml",
        "views/fila_excecao_views.xml",
        "views/integracao_log_views.xml",
        "views/res_partner_views.xml",
        "views/config_cobranca_views.xml",
        "views/portal_pagamento_templates.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "maisfibra_financeiro/static/src/components/financeiro_dashboard/*.js",
            "maisfibra_financeiro/static/src/components/financeiro_dashboard/*.xml",
            "maisfibra_financeiro/static/src/components/financeiro_dashboard/*.scss",
        ],
    },
    "installable": True,
    "application": True,
}
