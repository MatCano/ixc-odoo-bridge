{
    "name": "MaisFibra - Financeiro",
    "version": "19.0.1.0.0",
    "summary": "Aprovação multinível de contas a pagar, agenda de pagamentos, fluxo de caixa previsto, DRE gerencial e centros de custo",
    "description": """
Base da Frente 3 que NÃO duplica o IXC (o IXC segue como contas a receber do provedor):
- Contas a pagar: aprovação por alçada (Gerente até R$ 5 mil, Diretor até R$ 20 mil, CFO acima),
  configurável; conta não pode ser lançada sem aprovação do nível certo
- Agenda de pagamentos (calendário de vencimentos, vence na semana, atrasados)
- Fluxo de caixa previsto (a receber x a pagar por vencimento)
- DRE gerencial por mês (receitas, custos, despesas)
- Plano analítico "Centros de Custo" (um por CNPJ)
Aprovação DESLIGADA por padrão (Definições > MaisFibra).
    """,
    "author": "MaisFibra",
    "category": "Accounting",
    "license": "LGPL-3",
    "depends": ["account", "analytic", "maisfibra_crm"],
    "data": [
        "security/groups.xml",
        "security/ir.model.access.csv",
        "data/analytic_data.xml",
        "views/aprovacao_views.xml",
        "views/relatorios_views.xml",
    ],
    "installable": True,
    "application": False,
}
