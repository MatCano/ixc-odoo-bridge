"""Log estruturado e sanitizado das integrações financeiras e fiscais."""
import json
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .boleto_pdf_engine import sanitizar_dict


class IntegracaoLog(models.Model):
    _name = "maisfibra.integracao.log"
    _description = "Log de Integração Financeira e Fiscal"
    _order = "id desc"

    data_hora = fields.Datetime("Data/Hora", default=fields.Datetime.now, readonly=True, index=True)
    operacao = fields.Char("Operação", required=True, readonly=True, index=True)
    direcao = fields.Selection(
        [("outbound", "Saída (Odoo → Externo)"), ("inbound", "Entrada (Externo → Odoo)")],
        string="Direção", default="outbound", required=True, readonly=True,
    )
    identificador_externo = fields.Char("ID Externo", readonly=True, index=True)
    cobranca_id = fields.Many2one("maisfibra.cobranca", "Cobrança", readonly=True, ondelete="set null", index=True)
    nota_fiscal_id = fields.Many2one("maisfibra.nota.fiscal", "Nota Fiscal", readonly=True, ondelete="set null", index=True)
    partner_id = fields.Many2one("res.partner", "Cliente", readonly=True, ondelete="set null", index=True)
    status = fields.Selection(
        [("sucesso", "Sucesso"), ("falha", "Falha"), ("aviso", "Aviso")],
        string="Resultado", required=True, readonly=True, index=True,
    )
    http_status = fields.Integer("HTTP", readonly=True)
    payload_resumo = fields.Text("Payload (sanitizado)", readonly=True)
    resposta_resumo = fields.Text("Resposta (sanitizada)", readonly=True)
    motivo_falha = fields.Text("Motivo da falha", readonly=True)
    duracao_ms = fields.Integer("Duração (ms)", readonly=True)
    user_id = fields.Many2one("res.users", "Usuário", default=lambda self: self.env.uid, readonly=True)

    @api.model
    def registrar(
        self, operacao, status, direcao="outbound", identificador_externo="",
        cobranca=None, nota_fiscal=None, partner=None, http_status=0,
        payload=None, resposta=None, motivo_falha="", duracao_ms=0,
    ):
        def _dump(v):
            if v is None or v == "":
                return ""
            limpo = sanitizar_dict(v) if isinstance(v, (dict, list)) else str(v)[:1000]
            try:
                return json.dumps(limpo, ensure_ascii=False, indent=2) if isinstance(limpo, (dict, list)) else str(limpo)
            except TypeError:
                return str(limpo)[:1000]

        vals = {
            "operacao": operacao,
            "direcao": direcao,
            "identificador_externo": str(identificador_externo or "")[:120],
            "cobranca_id": cobranca.id if cobranca else False,
            "nota_fiscal_id": nota_fiscal.id if nota_fiscal else False,
            "partner_id": partner.id if partner else (cobranca.partner_id.id if cobranca else (nota_fiscal.partner_id.id if nota_fiscal else False)),
            "status": status,
            "http_status": int(http_status or 0),
            "payload_resumo": _dump(payload),
            "resposta_resumo": _dump(resposta),
            "motivo_falha": str(motivo_falha or "")[:2000],
            "duracao_ms": int(duracao_ms or 0),
            "user_id": self.env.uid,
        }
        return self.sudo().with_context(mf_log_create=True).create(vals)

    def write(self, vals):
        if not self.env.context.get("mf_log_internal"):
            raise UserError(_("Logs de integração são imutáveis."))
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get("mf_log_purge"):
            raise UserError(_("Logs de integração não podem ser removidos manualmente."))
        return super().unlink()

    @api.model
    def _cron_limpar_logs_antigos(self, dias=180):
        limite = fields.Datetime.now() - timedelta(days=dias)
        antigos = self.sudo().search([("data_hora", "<", limite)], limit=10000)
        if antigos:
            antigos.with_context(mf_log_purge=True).unlink()
