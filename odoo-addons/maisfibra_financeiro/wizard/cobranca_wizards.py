"""Wizards operacionais para Lote de Cobranças, Baixa Manual e Cancelamento/Substituição."""
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CobrancaLoteWizard(models.TransientModel):
    _name = "maisfibra.cobranca.lote.wizard"
    _description = "Gerar Cobranças Recorrentes em Lote"

    competencia = fields.Char("Competência (AAAA-MM)", required=True, default=lambda self: date.today().strftime("%Y-%m"))
    contrato_ids = fields.Many2many(
        "maisfibra.contrato.recorrente",
        relation="mf_cobranca_lote_contrato_rel",
        string="Contratos",
        domain=[("state", "=", "ativo")],
        default=lambda self: self.env["maisfibra.contrato.recorrente"].search([("state", "=", "ativo")]),
    )
    emitir_boletos = fields.Boolean("Emitir boletos imediatamente", default=True)
    enviar_clientes = fields.Boolean("Enviar automaticamente aos clientes", default=False)

    def action_executar(self):
        self.ensure_one()
        contratos = self.contrato_ids or self.env["maisfibra.contrato.recorrente"].search([("state", "=", "ativo")])
        if not contratos:
            raise UserError(_("Nenhum contrato ativo encontrado para gerar cobranças."))

        cobrancas = self.env["maisfibra.cobranca"]
        for ctr in contratos:
            cobrancas |= ctr.action_gerar_cobranca_competencia(
                competencia=self.competencia,
                emitir=self.emitir_boletos,
                enviar=self.enviar_clientes,
            )

        return {
            "type": "ir.actions.act_window",
            "name": _("Cobranças — %s", self.competencia),
            "res_model": "maisfibra.cobranca",
            "view_mode": "list,kanban,form",
            "domain": [("id", "in", cobrancas.ids)],
        }


class CobrancaPagamentoWizard(models.TransientModel):
    _name = "maisfibra.cobranca.pagamento.wizard"
    _description = "Registrar Pagamento de Cobrança"

    cobranca_id = fields.Many2one("maisfibra.cobranca", "Cobrança", required=True)
    currency_id = fields.Many2one("res.currency", related="cobranca_id.currency_id")
    data_pagamento = fields.Date("Data do Pagamento", default=fields.Date.today, required=True)
    valor_original = fields.Monetary("Valor Original", related="cobranca_id.valor_original", currency_field="currency_id", readonly=True)
    multa_calculada = fields.Monetary("Multa Prevista", currency_field="currency_id", compute="_compute_encargos")
    juros_calculados = fields.Monetary("Juros Previstos", currency_field="currency_id", compute="_compute_encargos")
    valor_pago = fields.Monetary("Valor Recebido (R$)", currency_field="currency_id", required=True)

    @api.depends("cobranca_id", "data_pagamento")
    def _compute_encargos(self):
        for w in self:
            if w.cobranca_id and w.data_pagamento:
                enc = w.cobranca_id.calcular_encargos(data_referencia=w.data_pagamento)
                w.multa_calculada = enc["multa"]
                w.juros_calculados = enc["juros"]
            else:
                w.multa_calculada = 0.0
                w.juros_calculados = 0.0

    @api.onchange("cobranca_id", "data_pagamento")
    def _onchange_valor(self):
        if self.cobranca_id:
            enc = self.cobranca_id.calcular_encargos(data_referencia=self.data_pagamento or date.today())
            self.valor_pago = enc["total"]

    def action_confirmar(self):
        self.ensure_one()
        self.cobranca_id.action_registrar_pagamento(
            valor_pago=self.valor_pago,
            data_pagamento=self.data_pagamento,
        )
        return {"type": "ir.actions.act_window_close"}


class CobrancaCancelarWizard(models.TransientModel):
    _name = "maisfibra.cobranca.cancelar.wizard"
    _description = "Cancelar ou Substituir Documento Financeiro/Fiscal"

    alvo = fields.Selection(
        [
            ("cobranca", "Cancelar Cobrança"),
            ("nf_cancelar", "Cancelar Nota Fiscal"),
            ("nf_substituir", "Substituir Nota Fiscal"),
        ],
        string="Operação", required=True, default="cobranca",
    )
    cobranca_id = fields.Many2one("maisfibra.cobranca", "Cobrança")
    nota_fiscal_id = fields.Many2one("maisfibra.nota.fiscal", "Nota Fiscal")
    motivo = fields.Text("Motivo / Justificativa", required=True)
    novo_valor = fields.Float("Novo Valor (para substituição)", digits=(16, 2))

    def action_confirmar(self):
        self.ensure_one()
        if self.alvo == "cobranca":
            if not self.cobranca_id:
                raise UserError(_("Selecione a cobrança."))
            self.cobranca_id.action_cancelar(motivo=self.motivo)
        elif self.alvo == "nf_cancelar":
            if not self.nota_fiscal_id:
                raise UserError(_("Selecione a Nota Fiscal."))
            self.nota_fiscal_id.action_cancelar(motivo=self.motivo)
        elif self.alvo == "nf_substituir":
            if not self.nota_fiscal_id:
                raise UserError(_("Selecione a Nota Fiscal."))
            nova = self.nota_fiscal_id.action_substituir(
                motivo=self.motivo,
                novo_valor=self.novo_valor if self.novo_valor > 0 else None,
            )
            return {
                "type": "ir.actions.act_window",
                "res_model": "maisfibra.nota.fiscal",
                "res_id": nova.id,
                "view_mode": "form",
                "target": "current",
            }
        return {"type": "ir.actions.act_window_close"}
