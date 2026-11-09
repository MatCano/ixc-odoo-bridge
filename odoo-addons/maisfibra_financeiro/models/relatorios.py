"""Relatórios financeiros gerenciais (visões SQL sobre os lançamentos do Odoo).

No Odoo 19 o tipo da conta não é guardado na linha do lançamento, por isso as
visões leem account_account.account_type direto.
"""
from odoo import fields, models, tools


class FluxoCaixaPrevisto(models.Model):
    _name = "maisfibra.fluxo.caixa"
    _description = "Fluxo de caixa previsto"
    _auto = False
    _order = "vencimento"

    vencimento = fields.Date("Vencimento", readonly=True)
    tipo = fields.Selection([("receber", "A receber"), ("pagar", "A pagar")], readonly=True)
    partner_id = fields.Many2one("res.partner", "Parceiro", readonly=True)
    move_id = fields.Many2one("account.move", "Documento", readonly=True)
    company_id = fields.Many2one("res.company", "Empresa", readonly=True)
    valor = fields.Float("Saldo (+ entra / − sai)", readonly=True, aggregator="sum")
    atrasado = fields.Boolean("Atrasado", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT l.id, COALESCE(l.date_maturity, l.date) AS vencimento,
                       CASE WHEN a.account_type = 'asset_receivable' THEN 'receber' ELSE 'pagar' END AS tipo,
                       l.partner_id, l.move_id, l.company_id,
                       l.amount_residual AS valor,
                       COALESCE(l.date_maturity, l.date) < CURRENT_DATE AS atrasado
                  FROM account_move_line l
                  JOIN account_move m ON m.id = l.move_id
                  JOIN account_account a ON a.id = l.account_id
                 WHERE m.state = 'posted'
                   AND a.account_type IN ('asset_receivable', 'liability_payable')
                   AND l.amount_residual <> 0
            )
        """)


class DreGerencial(models.Model):
    _name = "maisfibra.dre"
    _description = "DRE gerencial"
    _auto = False
    _order = "data desc"

    data = fields.Date("Data", readonly=True)
    categoria = fields.Selection(
        [("1_receita", "1. Receitas"), ("2_custo", "2. Custos diretos"),
         ("3_despesa", "3. Despesas operacionais"), ("4_depreciacao", "4. Depreciação")],
        string="Linha da DRE", readonly=True)
    account_id = fields.Many2one("account.account", "Conta", readonly=True)
    company_id = fields.Many2one("res.company", "Empresa", readonly=True)
    valor = fields.Float("Valor (+ receita / − custo e despesa)", readonly=True, aggregator="sum")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT l.id, l.date AS data, l.account_id, l.company_id,
                       CASE a.account_type
                            WHEN 'income' THEN '1_receita'
                            WHEN 'income_other' THEN '1_receita'
                            WHEN 'expense_direct_cost' THEN '2_custo'
                            WHEN 'expense_depreciation' THEN '4_depreciacao'
                            ELSE '3_despesa' END AS categoria,
                       -l.balance AS valor
                  FROM account_move_line l
                  JOIN account_move m ON m.id = l.move_id
                  JOIN account_account a ON a.id = l.account_id
                 WHERE m.state = 'posted'
                   AND a.account_type IN ('income', 'income_other', 'expense',
                                          'expense_direct_cost', 'expense_depreciation')
            )
        """)
