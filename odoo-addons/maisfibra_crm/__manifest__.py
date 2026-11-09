{
    "name": "MaisFibra - CRM",
    "version": "19.0.1.0.0",
    "summary": "Fluxo comercial MaisFibra: endereço de instalação, condomínio, prospect e negociação no IXC",
    "description": """
- Endereço de instalação (duplicado) em Contato e Lead
- Condomínio, bloco, apartamento (condomínios sincronizados do IXC)
- Origem (como nos conheceu), canal de venda, indicado por, concorrente
- Estágios com código: Recolher Documentos, Negociação, Aguardar Assinatura, Perdemos
- Ao entrar em Recolher Documentos: valida dados e vira PROSPECT no IXC (via bridge)
- Ao entrar em Negociação: valida dados e cria NEGOCIAÇÃO no IXC (via bridge)
- Ganho/perda vindo do IXC move o lead (feito pelo bridge)
- Botão de consulta de crédito
    """,
    "author": "MaisFibra",
    "category": "Sales/CRM",
    "license": "LGPL-3",
    "depends": ["base", "mail", "crm", "web", "sales_team"],
    "data": [
        "security/ir.model.access.csv",
        "data/mail_templates.xml",
        "views/ixc_consulta_views.xml",
        "views/maisfibra_cadastros_views.xml",
        "views/crm_stage_views.xml",
        "views/crm_lead_views.xml",
        "views/res_partner_views.xml",
        "views/res_config_settings_views.xml",
        "views/vendas_analise_views.xml",
        "views/ixc_vinculos_views.xml",
        "report/report_vendedores.xml",
    ],
    "external_dependencies": {"python": ["requests"]},
    "installable": True,
    "application": False,
}
