"""Retenção LGPD (docs/GOVERNANCA_E_LGPD.md §7): leads PERDIDOS sem alteração há N meses
(padrão 24) são anonimizados no Odoo. DESLIGADO por padrão.

- Não toca em contatos (res.partner): podem ser clientes ou ter faturas.
- Não toca no IXC (fonte de verdade tem sua própria política).
- Apaga o histórico (chatter) e as mensagens de WhatsApp do lead.
- Roda sem auditoria, senão a trilha guardaria os dados apagados.
"""
import logging
from datetime import timedelta

from odoo import _, api, fields, models

from .endereco_mixin import ENDERECO_FIELDS

_logger = logging.getLogger(__name__)
P = "maisfibra_crm."


class CrmLead(models.Model):
    _inherit = "crm.lead"

    mf_anonimizado_em = fields.Datetime("Anonimizado (LGPD) em", readonly=True, copy=False)


class Lgpd(models.AbstractModel):
    _name = "maisfibra.lgpd"
    _description = "Retenção LGPD de leads"

    @api.model
    def _dominio(self):
        meses = int(self.env["ir.config_parameter"].sudo().get_param(P + "lgpd_meses", "24") or 24)
        limite = fields.Datetime.now() - timedelta(days=30 * meses)
        return [("won_status", "=", "lost"), ("write_date", "<", limite), ("mf_anonimizado_em", "=", False)]

    @api.model
    def contar(self):
        return self.env["crm.lead"].sudo().with_context(active_test=False).search_count(self._dominio())

    @api.model
    def anonimizar(self, limite=500):
        Lead = self.env["crm.lead"].sudo().with_context(active_test=False, mf_audit_off=True, mf_from_bridge=True)
        leads = Lead.search(self._dominio(), limit=limite)
        vazio = {f: False for f in ENDERECO_FIELDS}
        for lead in leads:
            lead.message_ids.unlink()
            if "maisfibra.whatsapp.message" in self.env:
                self.env["maisfibra.whatsapp.message"].sudo().search([("lead_id", "=", lead.id)]).unlink()
            lead.write(dict(vazio, **{
                "name": f"[Anonimizado] #{lead.id}",
                "contact_name": False, "partner_name": False, "email_from": False, "phone": False,
                "description": False, "street": False, "street2": False, "zip": False, "city": False,
                "partner_id": False, "mf_cpf_cnpj": False, "mf_origem": False, "mf_concorrente": False,
                "mf_indicado_por_id": False,
                "mf_anonimizado_em": fields.Datetime.now(),
            }))
        _logger.info("LGPD: %s leads anonimizados", len(leads))
        return len(leads)

    @api.model
    def _cron_retencao(self):
        if self.env["ir.config_parameter"].sudo().get_param(P + "lgpd_ativo") in ("True", "1"):
            self.anonimizar()

    def _notificar(self, titulo, texto):
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"title": titulo, "message": texto, "sticky": True, "type": "info"}}


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    mf_lgpd_ativo = fields.Boolean("Anonimizar leads perdidos automaticamente", config_parameter=P + "lgpd_ativo")
    mf_lgpd_meses = fields.Integer("Após quantos meses sem alteração", default=24, config_parameter=P + "lgpd_meses")

    def action_mf_lgpd_simular(self):
        self.set_values()
        n = self.env["maisfibra.lgpd"].contar()
        return self.env["maisfibra.lgpd"]._notificar(
            _("Simulação LGPD"), _("%s leads perdidos seriam anonimizados agora. Nada foi alterado.", n))

    def action_mf_lgpd_executar(self):
        self.set_values()
        n = self.env["maisfibra.lgpd"].anonimizar()
        return self.env["maisfibra.lgpd"]._notificar(
            _("LGPD"), _("%s leads anonimizados (lote de até 500; rode de novo se houver mais).", n))
