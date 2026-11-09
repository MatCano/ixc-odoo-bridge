import logging

from markupsafe import Markup

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)
P = "maisfibra_csat."


def _param(env, key, default=""):
    return env["ir.config_parameter"].sudo().get_param(P + key, default)


class CrmStage(models.Model):
    _inherit = "crm.stage"

    mf_csat = fields.Boolean(
        "Enviar pesquisa de satisfação",
        help="Ao entrar neste estágio o cliente recebe o link da pesquisa (se o CSAT estiver ligado em Definições).",
    )


class CrmLead(models.Model):
    _inherit = "crm.lead"

    mf_csat_input_ids = fields.One2many("survey.user_input", "mf_lead_id", string="Pesquisas de satisfação")
    mf_csat_nota = fields.Integer("Nota de satisfação", readonly=True, aggregator="avg", copy=False)
    mf_csat_comentario = fields.Text("Comentário da pesquisa", readonly=True, copy=False)
    mf_csat_data = fields.Datetime("Respondida em", readonly=True, copy=False)
    mf_csat_status = fields.Selection(
        [("enviada", "Enviada"), ("respondida", "Respondida")],
        string="Pesquisa", readonly=True, copy=False,
    )

    def write(self, vals):
        res = super().write(vals)
        if "stage_id" in vals and _param(self.env, "ativo") in ("True", "1"):
            stage = self.env["crm.stage"].browse(vals["stage_id"])
            if stage.mf_csat:
                for lead in self.filtered(lambda l: l.mf_csat_status != "respondida"):
                    lead._mf_enviar_csat()
        return res

    def _mf_enviar_csat(self):
        self.ensure_one()
        survey = self.env.ref("maisfibra_csat.survey_csat", raise_if_not_found=False)
        if not survey:
            return
        resposta = survey.sudo()._create_answer(
            partner=self.partner_id, email=self.email_from or self.partner_id.email or False,
            check_attempts=False,
        )
        resposta.mf_lead_id = self.id
        url = resposta.get_start_url()
        base = self.get_base_url()
        link = url if url.startswith("http") else base + url
        nome = (self.contact_name or self.partner_id.name or "").split(" ")[0] or "tudo bem"
        texto = (_param(self.env, "mensagem") or
                 "Olá, {nome}! Como foi sua experiência com a Mais Fibra? Leva 10 segundos: {link}")
        texto = texto.replace("{nome}", nome).replace("{link}", link)

        canal = _param(self.env, "canal", "whatsapp")
        enviado = []
        if canal in ("whatsapp", "ambos") and "maisfibra.whatsapp.message" in self.env and self.phone:
            self.env["maisfibra.whatsapp.message"].enfileirar(self.phone, texto, lead=self)
            enviado.append("WhatsApp")
        if canal in ("email", "ambos") and (self.email_from or self.partner_id.email):
            tpl = self.env.ref("maisfibra_csat.mail_tpl_csat", raise_if_not_found=False)
            if tpl:
                tpl.sudo().with_context(mf_csat_link=link, mf_csat_nome=nome).send_mail(self.id)
                enviado.append("e-mail")
        if enviado:
            self.sudo().write({"mf_csat_status": "enviada"})
            self.message_post(body=_("📋 Pesquisa de satisfação enviada por %s.", " e ".join(enviado)),
                              subtype_xmlid="mail.mt_note")
        else:
            _logger.info("CSAT: lead %s sem canal disponível (telefone/e-mail/módulo)", self.id)


class SurveyUserInput(models.Model):
    _inherit = "survey.user_input"

    mf_lead_id = fields.Many2one("crm.lead", string="Lead", index=True, ondelete="set null")

    def _mark_done(self):
        res = super()._mark_done()
        survey = self.env.ref("maisfibra_csat.survey_csat", raise_if_not_found=False)
        for resp in self.filtered(lambda r: r.mf_lead_id and r.survey_id == survey):
            nota, comentario = 0, ""
            for linha in resp.user_input_line_ids:
                if linha.answer_type == "scale":
                    nota = linha.value_scale
                elif linha.answer_type in ("text_box", "char_box"):
                    comentario = linha.value_text_box or linha.value_char_box or ""
            lead = resp.mf_lead_id.sudo()
            lead.write({"mf_csat_nota": nota, "mf_csat_comentario": comentario,
                        "mf_csat_data": fields.Datetime.now(), "mf_csat_status": "respondida"})
            estrelas = "★" * nota + "☆" * (5 - nota)
            lead.message_post(
                body=Markup("<p><b>📋 Pesquisa respondida: {} ({}/5)</b></p><p>{}</p>").format(
                    estrelas, nota, comentario or ""),
                subtype_xmlid="mail.mt_note")
            if nota and nota <= 2:
                gerente = lead.team_id.user_id or lead.user_id or self.env.ref("base.user_admin")
                lead.activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=_("Cliente insatisfeito (nota %s) — fazer contato", nota),
                    note=comentario or "",
                    user_id=gerente.id,
                    date_deadline=fields.Date.today(),
                )
        return res


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    mf_csat_ativo = fields.Boolean("Pesquisa de satisfação automática", config_parameter=P + "ativo")
    mf_csat_canal = fields.Selection(
        [("whatsapp", "WhatsApp"), ("email", "E-mail"), ("ambos", "WhatsApp e e-mail")],
        string="Enviar por", default="whatsapp", config_parameter=P + "canal",
    )
    mf_csat_mensagem = fields.Char(
        "Mensagem (WhatsApp)", config_parameter=P + "mensagem",
        help="Use {nome} e {link}.",
        default="Olá, {nome}! Como foi sua experiência com a Mais Fibra? Leva 10 segundos: {link}",
    )
