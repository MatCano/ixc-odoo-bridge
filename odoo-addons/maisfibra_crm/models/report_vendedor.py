"""Análise de vendas: uma linha por lead (inclui ganhos e perdidos arquivados).

Uma linha por lead faz os agregados do pivot ficarem corretos (soma de contagens
e média de ciclo ignorando nulos) com qualquer filtro de período/funil/origem.
"""
from odoo import api, fields, models, tools


class VendasAnalise(models.Model):
    _name = "maisfibra.vendas.analise"
    _description = "Análise de vendas MaisFibra"
    _auto = False
    _order = "data desc"

    data = fields.Date("Data de criação", readonly=True)
    data_fechamento = fields.Date("Data de fechamento", readonly=True)
    user_id = fields.Many2one("res.users", "Vendedor", readonly=True)
    team_id = fields.Many2one("crm.team", "Funil", readonly=True)
    stage_id = fields.Many2one("crm.stage", "Estágio", readonly=True)
    source_id = fields.Many2one("utm.source", "Origem", readonly=True)
    lost_reason_id = fields.Many2one("crm.lost.reason", "Motivo de perda", readonly=True)
    lead_id = fields.Many2one("crm.lead", "Lead", readonly=True)
    status = fields.Selection([("won", "Ganho"), ("lost", "Perdido"), ("pending", "Em aberto")],
                              readonly=True)
    qtd = fields.Integer("Leads", readonly=True, aggregator="sum")
    qtd_oportunidade = fields.Integer("Oportunidades", readonly=True, aggregator="sum")
    qtd_ganho = fields.Integer("Ganhos", readonly=True, aggregator="sum")
    qtd_perdido = fields.Integer("Perdidos", readonly=True, aggregator="sum")
    qtd_aberto = fields.Integer("Em aberto", readonly=True, aggregator="sum")
    valor_esperado = fields.Float("Valor esperado", readonly=True, aggregator="sum")
    valor_ganho = fields.Float("Valor vendido", readonly=True, aggregator="sum")
    ciclo_dias = fields.Float("Ciclo de venda (dias)", readonly=True, aggregator="avg",
                              help="Da criação ao ganho. Média só dos ganhos.")
    dias_sem_mover = fields.Float("Dias parado no estágio", readonly=True, aggregator="avg")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT
                    l.id AS id,
                    l.id AS lead_id,
                    l.create_date::date AS data,
                    l.date_closed::date AS data_fechamento,
                    l.user_id, l.team_id, l.stage_id, l.source_id, l.lost_reason_id,
                    COALESCE(l.won_status, 'pending') AS status,
                    1 AS qtd,
                    CASE WHEN l.type = 'opportunity' THEN 1 ELSE 0 END AS qtd_oportunidade,
                    CASE WHEN l.won_status = 'won' THEN 1 ELSE 0 END AS qtd_ganho,
                    CASE WHEN l.won_status = 'lost' THEN 1 ELSE 0 END AS qtd_perdido,
                    CASE WHEN COALESCE(l.won_status, 'pending') = 'pending' THEN 1 ELSE 0 END AS qtd_aberto,
                    COALESCE(l.expected_revenue, 0) AS valor_esperado,
                    CASE WHEN l.won_status = 'won' THEN COALESCE(l.expected_revenue, 0) ELSE 0 END AS valor_ganho,
                    CASE WHEN l.won_status = 'won' AND l.date_closed IS NOT NULL
                         THEN EXTRACT(EPOCH FROM (l.date_closed - l.create_date)) / 86400.0 END AS ciclo_dias,
                    CASE WHEN COALESCE(l.won_status, 'pending') = 'pending' AND l.date_last_stage_update IS NOT NULL
                         THEN EXTRACT(EPOCH FROM (NOW() AT TIME ZONE 'UTC' - l.date_last_stage_update)) / 86400.0 END
                         AS dias_sem_mover
                FROM crm_lead l
            )
        """)


class RelatorioVendedoresWizard(models.TransientModel):
    _name = "maisfibra.relatorio.vendedores"
    _description = "Relatório de performance de vendedores"

    data_inicio = fields.Date("De", required=True,
                              default=lambda s: fields.Date.today().replace(day=1))
    data_fim = fields.Date("Até", required=True, default=fields.Date.today)
    team_ids = fields.Many2many("crm.team", string="Funis (vazio = todos)")

    def action_imprimir(self):
        self.ensure_one()
        return self.env.ref("maisfibra_crm.action_report_vendedores").report_action(self)

    def _linhas(self):
        """Uma linha por vendedor no período (pela data de criação do lead)."""
        self.ensure_one()
        params = [self.data_inicio, self.data_fim]
        filtro_funil = ""
        if self.team_ids:
            filtro_funil = "AND team_id = ANY(%s)"
            params.append(self.team_ids.ids)
        self.env.cr.execute(f"""
            SELECT user_id,
                   SUM(qtd), SUM(qtd_oportunidade), SUM(qtd_ganho), SUM(qtd_perdido),
                   SUM(qtd_aberto), SUM(valor_ganho), AVG(ciclo_dias)
              FROM maisfibra_vendas_analise
             WHERE data BETWEEN %s AND %s {filtro_funil}
          GROUP BY user_id
          ORDER BY SUM(valor_ganho) DESC, SUM(qtd_ganho) DESC
        """, params)
        users = self.env["res.users"]
        linhas = []
        for uid, leads, opp, ganho, perdido, aberto, valor, ciclo in self.env.cr.fetchall():
            fechados = (ganho or 0) + (perdido or 0)
            linhas.append({
                "vendedor": users.browse(uid).name if uid else "Sem vendedor",
                "leads": leads or 0, "oportunidades": opp or 0,
                "ganhos": ganho or 0, "perdidos": perdido or 0, "abertos": aberto or 0,
                "valor": valor or 0.0,
                "conversao": (100.0 * ganho / fechados) if fechados else 0.0,
                "ticket": (valor / ganho) if ganho else 0.0,
                "ciclo": ciclo,
            })
        return linhas


class ReportVendedores(models.AbstractModel):
    _name = "report.maisfibra_crm.report_vendedores"
    _description = "Relatório de vendedores (PDF)"

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env["maisfibra.relatorio.vendedores"].browse(docids)
        return {"docs": docs, "linhas": {d.id: d._linhas() for d in docs}}
