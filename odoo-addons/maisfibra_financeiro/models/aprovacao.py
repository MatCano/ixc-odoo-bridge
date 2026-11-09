"""Aprovação multinível de contas a pagar (Anexo I, Frente 3).

Alçadas (configuráveis em Definições > MaisFibra):
  até limite_gerente (R$ 5.000)   -> Gerente
  até limite_diretor (R$ 20.000)  -> Diretor
  acima                          -> CFO
Grupos são hierárquicos: CFO pode tudo, Diretor pode Diretor e Gerente.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

P = "maisfibra_financeiro."
NIVEIS = [("gerente", "Gerente"), ("diretor", "Diretor"), ("cfo", "CFO")]
GRUPO = {
    "gerente": "maisfibra_financeiro.group_aprovador_gerente",
    "diretor": "maisfibra_financeiro.group_aprovador_diretor",
    "cfo": "maisfibra_financeiro.group_aprovador_cfo",
}
TIPOS = ("in_invoice", "in_refund")


def _param(env, key, default):
    return env["ir.config_parameter"].sudo().get_param(P + key, default)


class AccountMove(models.Model):
    _inherit = "account.move"

    mf_nivel_aprovacao = fields.Selection(NIVEIS, "Alçada necessária",
                                          compute="_compute_mf_nivel", store=True)
    mf_aprovacao_state = fields.Selection(
        [("rascunho", "Não solicitada"), ("pendente", "Aguardando aprovação"),
         ("aprovado", "Aprovada"), ("recusado", "Recusada")],
        string="Aprovação", default="rascunho", copy=False, tracking=True,
    )
    mf_aprovado_por = fields.Many2one("res.users", "Aprovada por", copy=False, readonly=True)
    mf_aprovado_em = fields.Datetime("Aprovada em", copy=False, readonly=True)
    mf_valor_aprovado = fields.Monetary("Valor aprovado", copy=False, readonly=True,
                                        currency_field="currency_id")
    mf_motivo_recusa = fields.Text("Motivo da recusa", copy=False)
    mf_aprovacao_ativa = fields.Boolean(compute="_compute_mf_aprovacao_ativa")

    @api.depends("amount_total_signed", "move_type")
    def _compute_mf_nivel(self):
        lim_g = float(_param(self.env, "limite_gerente", "5000"))
        lim_d = float(_param(self.env, "limite_diretor", "20000"))
        for m in self:
            if m.move_type not in TIPOS:
                m.mf_nivel_aprovacao = False
                continue
            valor = abs(m.amount_total_signed)
            m.mf_nivel_aprovacao = "gerente" if valor <= lim_g else "diretor" if valor <= lim_d else "cfo"

    def _compute_mf_aprovacao_ativa(self):
        ativo = _param(self.env, "aprovacao_ativa", "False") in ("True", "1")
        for m in self:
            m.mf_aprovacao_ativa = ativo and m.move_type in TIPOS

    def _mf_pode_aprovar(self):
        self.ensure_one()
        return self.env.user.has_group(GRUPO[self.mf_nivel_aprovacao])

    def action_mf_solicitar_aprovacao(self):
        for m in self:
            if m.move_type not in TIPOS:
                continue
            m._compute_mf_nivel()
            m.mf_aprovacao_state = "pendente"
            grupo = self.env.ref(GRUPO[m.mf_nivel_aprovacao])
            aprovadores = grupo.all_user_ids if "all_user_ids" in grupo._fields else grupo.user_ids
            for u in aprovadores.filtered(lambda u: not u.share)[:5]:
                m.activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=_("Aprovar conta a pagar (%s)", dict(NIVEIS)[m.mf_nivel_aprovacao]),
                    note=_("%(f)s — %(v)s", f=m.partner_id.display_name, v=m.amount_total),
                    user_id=u.id,
                )
            m.message_post(body=_("Aprovação solicitada (alçada: %s).", dict(NIVEIS)[m.mf_nivel_aprovacao]),
                           subtype_xmlid="mail.mt_note")

    def action_mf_aprovar(self):
        for m in self:
            if not m._mf_pode_aprovar():
                raise UserError(_("Esta conta exige aprovação de %s. Você não tem essa alçada.",
                                  dict(NIVEIS)[m.mf_nivel_aprovacao]))
            m.write({"mf_aprovacao_state": "aprovado", "mf_aprovado_por": self.env.uid,
                     "mf_aprovado_em": fields.Datetime.now(), "mf_valor_aprovado": m.amount_total})
            m.activity_ids.filtered(lambda a: "Aprovar conta" in (a.summary or "")).action_feedback(
                feedback=_("Aprovada"))
            m.message_post(body=_("✅ Aprovada por %s.", self.env.user.name), subtype_xmlid="mail.mt_note")

    def action_mf_recusar(self):
        for m in self:
            if not m._mf_pode_aprovar():
                raise UserError(_("Você não tem alçada para recusar esta conta."))
            m.write({"mf_aprovacao_state": "recusado"})
            m.activity_ids.filtered(lambda a: "Aprovar conta" in (a.summary or "")).action_feedback(
                feedback=_("Recusada"))
            m.message_post(body=_("❌ Recusada por %(u)s. %(r)s", u=self.env.user.name,
                                  r=m.mf_motivo_recusa or ""), subtype_xmlid="mail.mt_note")

    def action_post(self):
        if _param(self.env, "aprovacao_ativa", "False") in ("True", "1"):
            for m in self.filtered(lambda x: x.move_type in TIPOS):
                aprovado = m.mf_aprovacao_state == "aprovado" and float_compare(
                    m.mf_valor_aprovado, m.amount_total, precision_rounding=m.currency_id.rounding or 0.01) == 0
                if not aprovado:
                    if m.mf_aprovacao_state == "aprovado":
                        m.mf_aprovacao_state = "rascunho"  # valor mudou depois da aprovação
                    raise UserError(_(
                        "A conta de %(f)s (%(v)s) precisa ser aprovada por %(n)s antes de ser lançada. "
                        "Use o botão 'Solicitar aprovação'.",
                        f=m.partner_id.display_name or "-", v=m.amount_total,
                        n=dict(NIVEIS).get(m.mf_nivel_aprovacao, "-")))
        return super().action_post()


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    mf_aprovacao_ativa = fields.Boolean("Exigir aprovação de contas a pagar",
                                        config_parameter=P + "aprovacao_ativa")
    mf_limite_gerente = fields.Float("Gerente aprova até (R$)", default=5000,
                                     config_parameter=P + "limite_gerente")
    mf_limite_diretor = fields.Float("Diretor aprova até (R$)", default=20000,
                                     config_parameter=P + "limite_diretor")
