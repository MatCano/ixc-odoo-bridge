"""Disparos automáticos de WhatsApp e chatbot básico.

Tudo DESLIGADO por padrão (Definições > WhatsApp). Regras:
- Boas-vindas: lead novo criado por pessoa, formulário ou WhatsApp — nunca pelo
  sincronismo com o IXC (evita disparar para a base antiga no primeiro deploy).
- Mudança de estágio: se o estágio tem "Mensagem de WhatsApp", ela é enviada.
- Chatbot: responde quem escreve, com menu; "falar com atendente" pausa o bot
  para aquele lead e cria uma atividade para o vendedor.
"""
import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)
P = "maisfibra_whatsapp."


def _param(env, key, default=""):
    return env["ir.config_parameter"].sudo().get_param(P + key, default)


def _ligado(env, key):
    return _param(env, key) in ("True", "1", "true")


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def renderizar(texto, lead):
    nome = (lead.contact_name or lead.partner_id.name or "").split(" ")[0] or "tudo bem"
    return (texto or "").format_map(_SafeDict(
        nome=nome,
        vendedor=lead.user_id.name or "nossa equipe",
        empresa=lead.company_id.name or "",
        estagio=lead.stage_id.name or "",
    ))


class CrmStage(models.Model):
    _inherit = "crm.stage"

    wa_mensagem = fields.Text(
        "Mensagem de WhatsApp ao entrar",
        help="Enviada ao cliente quando o lead entra neste estágio (se a automação estiver ligada). "
             "Variáveis: {nome}, {vendedor}, {empresa}, {estagio}.",
    )


class CrmLead(models.Model):
    _inherit = "crm.lead"

    wa_bot_pausado_ate = fields.Datetime("Chatbot pausado até", copy=False)

    def _wa_telefone(self):
        return self.phone or self.partner_id.phone or ""

    def _wa_pausar_bot(self):
        horas = int(_param(self.env, "bot_pausa_horas", "12") or 12)
        self.sudo().write({"wa_bot_pausado_ate": fields.Datetime.now() + timedelta(hours=horas)})

    @api.model_create_multi
    def create(self, vals_list):
        leads = super().create(vals_list)
        ctx = self.env.context
        if _ligado(self.env, "auto_boas_vindas") and not ctx.get("mf_from_bridge") \
                and not ctx.get("wa_sem_boas_vindas"):
            texto = _param(self.env, "msg_boas_vindas")
            Msg = self.env["maisfibra.whatsapp.message"]
            for lead in leads:
                if lead._wa_telefone() and texto:
                    Msg.enfileirar(lead._wa_telefone(), renderizar(texto, lead), lead=lead)
        return leads

    def write(self, vals):
        res = super().write(vals)
        if "stage_id" in vals and _ligado(self.env, "auto_estagio"):
            stage = self.env["crm.stage"].browse(vals["stage_id"])
            if stage.wa_mensagem:
                Msg = self.env["maisfibra.whatsapp.message"]
                for lead in self:
                    if lead._wa_telefone():
                        Msg.enfileirar(lead._wa_telefone(), renderizar(stage.wa_mensagem, lead), lead=lead)
        return res


class WhatsappBot(models.AbstractModel):
    _name = "maisfibra.whatsapp.bot"
    _description = "Chatbot básico de WhatsApp"

    OPCOES = {
        "1": ("bot_resp_contratar", "Ótimo! Um consultor vai falar com você em instantes para montar a melhor oferta. 😉", True),
        "2": ("bot_resp_cliente", "Para 2ª via de boleto e suporte técnico, acesse a Central do Assinante ou responda 3 para falar com a equipe.", False),
        "3": ("bot_resp_atendente", "Certo! Já chamei uma pessoa da equipe para falar com você. 🙋", True),
    }
    MENU_PADRAO = (
        "Olá, {nome}! Aqui é a Mais Fibra. 👋\n"
        "Responda com o número:\n"
        "1 - Quero contratar internet\n"
        "2 - Já sou cliente (boleto/suporte)\n"
        "3 - Falar com um atendente"
    )

    @api.model
    def responder(self, lead, numero, texto):
        if not _ligado(self.env, "bot_ativo"):
            return
        agora = fields.Datetime.now()
        if lead.wa_bot_pausado_ate and lead.wa_bot_pausado_ate > agora:
            return  # humano conversando
        Msg = self.env["maisfibra.whatsapp.message"]
        escolha = (texto or "").strip()[:2].strip(" .-)")
        if escolha in self.OPCOES:
            chave, padrao, escalar = self.OPCOES[escolha]
            Msg.enfileirar(numero, renderizar(_param(self.env, chave) or padrao, lead), lead=lead, origem="bot")
            if escalar:
                self._escalar(lead, escolha)
            return
        menu = _param(self.env, "bot_menu") or self.MENU_PADRAO
        Msg.enfileirar(numero, renderizar(menu, lead), lead=lead, origem="bot")

    @api.model
    def _escalar(self, lead, escolha):
        lead._wa_pausar_bot()
        motivo = {"1": _("quer contratar"), "3": _("pediu atendente")}.get(escolha, "")
        user = lead.user_id or lead.team_id.user_id or self.env.ref("base.user_admin")
        lead.sudo().activity_schedule(
            "mail.mail_activity_data_todo",
            summary=_("Responder WhatsApp (%s)", motivo),
            note=_("Cliente %s pelo chatbot. O bot fica pausado para este lead.", motivo),
            user_id=user.id,
            date_deadline=fields.Date.today(),
        )
