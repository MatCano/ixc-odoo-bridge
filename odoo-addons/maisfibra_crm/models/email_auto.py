"""E-mails automáticos (boas-vindas e confirmações por estágio).

DESLIGADO por padrão (Definições > MaisFibra) — só ligar depois de configurar o
servidor SMTP de saída. Os e-mails entram na fila padrão do Odoo (mail.mail),
que já faz novas tentativas em caso de falha.
"""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


def _ligado(env, key):
    return env["ir.config_parameter"].sudo().get_param("maisfibra_crm." + key) in ("True", "1", "true")


class CrmStage(models.Model):
    _inherit = "crm.stage"

    mf_email_template_id = fields.Many2one(
        "mail.template", string="E-mail ao entrar no estágio",
        domain=[("model", "=", "crm.lead")],
        help="Enviado ao cliente quando o lead entra neste estágio (se os e-mails automáticos estiverem ligados).",
    )


class ResPartner(models.Model):
    _inherit = "res.partner"

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        # Nunca para contatos vindos do sincronismo com o IXC (base antiga).
        if _ligado(self.env, "email_boas_vindas") and not self.env.context.get("mf_from_bridge"):
            tpl = self.env.ref("maisfibra_crm.mail_tpl_boas_vindas", raise_if_not_found=False)
            for p in partners:
                if tpl and p.email and not p.is_company:
                    tpl.sudo().send_mail(p.id)
        return partners


class CrmLead(models.Model):
    _inherit = "crm.lead"

    def write(self, vals):
        res = super().write(vals)
        if "stage_id" in vals and _ligado(self.env, "email_estagio"):
            tpl = self.env["crm.stage"].browse(vals["stage_id"]).mf_email_template_id
            if tpl:
                for lead in self.filtered("email_from"):
                    tpl.sudo().send_mail(lead.id)
        return res
