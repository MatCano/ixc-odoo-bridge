{
    "name": "MaisFibra - Auditoria",
    "version": "19.0.1.0.0",
    "summary": "Registro imutável de alterações (quem, quando, o quê) em leads, contatos, faturas e estágios",
    "description": """
Atende ao item "Sistema de Auditoria Completo" do contrato (Frente 0):
- Registra criação, alteração (valor antigo → novo, por campo) e exclusão
- Leads/oportunidades, contatos, faturas/lançamentos e estágios do CRM
- Registros somente leitura para todos (inclusive administradores, pela interface)
- Retenção mínima de 12 meses (limpeza automática só do que passar de 13 meses)
    """,
    "author": "MaisFibra",
    "category": "Productivity",
    "license": "LGPL-3",
    "depends": ["base", "crm", "account"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/audit_log_views.xml",
    ],
    "installable": True,
    "application": False,
}
