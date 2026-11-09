{
    "name": "MaisFibra - Pesquisa de Satisfação (CSAT)",
    "version": "19.0.1.0.0",
    "summary": "Envia pesquisa de satisfação (nota 1 a 5) por WhatsApp/e-mail ao entrar num estágio e traz a nota para o lead",
    "description": """
Substitui o envio de CSAT feito hoje fora do Odoo:
- Pesquisa pronta (nota 1-5 + comentário) no app Pesquisas
- Marque "Enviar pesquisa de satisfação" no estágio (ex.: após a instalação)
- Link personalizado por cliente, enviado por WhatsApp (se o módulo existir) e/ou e-mail
- A nota volta para o lead; nota 1-2 cria atividade de retorno para o gerente
- Desligado por padrão (Definições > MaisFibra)
    """,
    "author": "MaisFibra",
    "category": "Sales/CRM",
    "license": "LGPL-3",
    "depends": ["maisfibra_crm", "survey"],
    "data": [
        "data/survey_data.xml",
        "views/csat_views.xml",
    ],
    "installable": True,
    "application": False,
}
