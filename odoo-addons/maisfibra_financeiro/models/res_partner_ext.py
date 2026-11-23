"""Extensão de res.partner com smart buttons e resumo de boletos/contratos/notas fiscais."""
from odoo import _, api, fields, models


class ResPartnerFinanceiro(models.Model):
    _inherit = "res.partner"

    mf_contrato_ids = fields.One2many("maisfibra.contrato.recorrente", "partner_id", "Contratos Recorrentes")
    mf_cobranca_ids = fields.One2many("maisfibra.cobranca", "partner_id", "Cobranças / Boletos")
    mf_nota_fiscal_ids = fields.One2many("maisfibra.nota.fiscal", "partner_id", "Notas Fiscais")

    mf_contrato_count = fields.Integer("Qtd. Contratos", compute="_compute_mf_financeiro_counts")
    mf_cobranca_count = fields.Integer("Qtd. Cobranças", compute="_compute_mf_financeiro_counts")
    mf_cobranca_vencida_count = fields.Integer("Cobranças Vencidas", compute="_compute_mf_financeiro_counts")
    mf_nf_count = fields.Integer("Qtd. Notas Fiscais", compute="_compute_mf_financeiro_counts")

    @api.depends("mf_contrato_ids", "mf_cobranca_ids.state", "mf_nota_fiscal_ids")
    def _compute_mf_financeiro_counts(self):
        for p in self:
            p.mf_contrato_count = len(p.mf_contrato_ids)
            p.mf_cobranca_count = len(p.mf_cobranca_ids)
            p.mf_cobranca_vencida_count = len(p.mf_cobranca_ids.filtered(lambda c: c.state == "vencida"))
            p.mf_nf_count = len(p.mf_nota_fiscal_ids)

    def action_abrir_mf_contratos(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Contratos — %s", self.name),
            "res_model": "maisfibra.contrato.recorrente",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }

    def action_abrir_mf_cobrancas(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Boletos e Cobranças — %s", self.name),
            "res_model": "maisfibra.cobranca",
            "view_mode": "list,kanban,form",
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }

    def action_abrir_mf_notas_fiscais(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Notas Fiscais — %s", self.name),
            "res_model": "maisfibra.nota.fiscal",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }
