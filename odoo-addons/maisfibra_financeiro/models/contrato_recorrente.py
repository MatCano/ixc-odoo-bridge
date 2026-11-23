"""Contratos recorrentes para controle de cobranças periódicas, pró-rata e descontos."""
import calendar
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class ContratoRecorrente(models.Model):
    _name = "maisfibra.contrato.recorrente"
    _description = "Contrato Recorrente de Cobrança"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char("Código", required=True, copy=False, readonly=True, default="Novo Contrato")
    partner_id = fields.Many2one("res.partner", "Cliente", required=True, ondelete="restrict", tracking=True, index=True)
    company_id = fields.Many2one("res.company", "Empresa", default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id", store=True)
    ixc_contrato_id = fields.Char("ID Contrato IXC", copy=False, index=True)
    ixc_cliente_id = fields.Char("ID Cliente IXC", related="partner_id.mf_ixc_cliente_id", store=True, readonly=True)
    plano_nome = fields.Char("Plano / Serviço", required=True, tracking=True)
    valor_mensal = fields.Monetary("Valor Mensal", currency_field="currency_id", required=True, tracking=True)
    dia_vencimento = fields.Integer("Dia de Vencimento", default=10, required=True)
    data_inicio = fields.Date("Início da Vigência", default=fields.Date.today, required=True)
    data_fim = fields.Date("Término da Vigência")
    periodicidade = fields.Selection(
        [("mensal", "Mensal"), ("trimestral", "Trimestral"), ("semestral", "Semestral"), ("anual", "Anual")],
        string="Periodicidade", default="mensal", required=True,
    )
    prorata_ativa = fields.Boolean("Aplicar Pró-rata no 1º mês", default=True)
    desconto_pontualidade = fields.Monetary("Desconto Pontualidade", currency_field="currency_id", default=0.0)
    multa_percentual = fields.Float("Multa por atraso (%)", digits=(5, 2), default=2.0)
    juros_mensal_percentual = fields.Float("Juros ao mês (%)", digits=(5, 2), default=1.0)
    canal_envio = fields.Selection(
        [("herdar", "Seguir padrão global"), ("email", "Somente E-mail"), ("whatsapp", "Somente WhatsApp"), ("ambos", "E-mail e WhatsApp")],
        string="Canal Preferencial", default="herdar", required=True,
    )
    consentimento_envio = fields.Boolean("Consentimento de envio (LGPD)", default=True, tracking=True)
    state = fields.Selection(
        [("rascunho", "Rascunho"), ("ativo", "Ativo"), ("suspenso", "Suspenso"), ("cancelado", "Cancelado")],
        string="Status", default="ativo", required=True, tracking=True, index=True,
    )
    cobranca_ids = fields.One2many("maisfibra.cobranca", "contrato_id", "Cobranças")
    cobranca_count = fields.Integer("Qtd. Cobranças", compute="_compute_cobranca_count")

    @api.depends("cobranca_ids")
    def _compute_cobranca_count(self):
        for rec in self:
            rec.cobranca_count = len(rec.cobranca_ids)

    @api.constrains("dia_vencimento", "valor_mensal", "ixc_contrato_id")
    def _check_contrato_validacoes(self):
        for rec in self:
            if not (1 <= rec.dia_vencimento <= 31):
                raise ValidationError(_("Dia de vencimento deve estar entre 1 e 31."))
            if rec.valor_mensal < 0:
                raise ValidationError(_("Valor mensal não pode ser negativo."))
            if rec.ixc_contrato_id:
                dup = self.search_count([("ixc_contrato_id", "=", rec.ixc_contrato_id), ("id", "!=", rec.id)])
                if dup:
                    raise ValidationError(_("Já existe um contrato cadastrado com o ID IXC %s.", rec.ixc_contrato_id))

    @api.model_create_multi
    def create(self, vals_list):
        seq = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == "Novo Contrato":
                vals["name"] = seq.next_by_code("maisfibra.contrato.recorrente") or "CTR/NEW"
        return super().create(vals_list)

    def calcular_valor_competencia(self, competencia):
        self.ensure_one()
        valor = float(self.valor_mensal or 0.0)
        if not self.prorata_ativa or not self.data_inicio:
            return round(valor, 2)

        comp_inicio = self.data_inicio.strftime("%Y-%m")
        if competencia == comp_inicio and self.data_inicio.day > 1:
            ano, mes = map(int, competencia.split("-"))
            dias_mes = calendar.monthrange(ano, mes)[1]
            dias_ativos = dias_mes - self.data_inicio.day + 1
            return round(valor * (dias_ativos / dias_mes), 2)
        return round(valor, 2)

    def _data_vencimento_competencia(self, competencia):
        self.ensure_one()
        ano, mes = map(int, competencia.split("-"))
        ultimo_dia = calendar.monthrange(ano, mes)[1]
        dia = min(max(1, self.dia_vencimento or 10), ultimo_dia)
        return date(ano, mes, dia)

    def action_gerar_cobranca_competencia(self, competencia=None, emitir=False, enviar=False):
        self.ensure_one()
        if self.state != "ativo":
            raise UserError(_("Apenas contratos ativos podem gerar cobranças."))

        comp = (competencia or date.today().strftime("%Y-%m"))[:7]
        chave = f"{self.partner_id.id}|{self.id}|{comp}|via1"
        Cobranca = self.env["maisfibra.cobranca"]
        existente = Cobranca.search([("chave_idempotencia", "=", chave)], limit=1)
        if existente:
            cob = existente
        else:
            valor = self.calcular_valor_competencia(comp)
            desconto = min(float(self.desconto_pontualidade or 0.0), valor) if self.desconto_pontualidade else 0.0
            venc = self._data_vencimento_competencia(comp)
            cob = Cobranca.create({
                "partner_id": self.partner_id.id,
                "contrato_id": self.id,
                "company_id": self.company_id.id,
                "competencia": comp,
                "data_vencimento": venc,
                "valor_original": valor,
                "valor_desconto": desconto,
                "chave_idempotencia": chave,
                "descricao": f"{self.plano_nome} — Ref. {comp}",
            })

        if emitir and cob.state in ("criada", "falha"):
            cob.action_emitir_boleto()
        if enviar and cob.state == "registrada":
            cob.action_enviar_cliente(tipo_disparo="automatico")
        return cob

    def action_abrir_cobrancas(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Cobranças — %s", self.name),
            "res_model": "maisfibra.cobranca",
            "view_mode": "list,form",
            "domain": [("contrato_id", "=", self.id)],
            "context": {"default_contrato_id": self.id, "default_partner_id": self.partner_id.id},
        }

    @api.model
    def _cron_gerar_cobrancas_mensais(self):
        ICP = self.env["ir.config_parameter"].sudo()
        if ICP.get_param("maisfibra_financeiro.geracao_automatica_ativa", "False") not in ("True", "1", "true"):
            return 0
        comp = date.today().strftime("%Y-%m")
        auto_emitir = ICP.get_param("maisfibra_financeiro.emissao_automatica_ativa", "False") in ("True", "1", "true")
        auto_enviar = ICP.get_param("maisfibra_financeiro.envio_automatico_ativo", "False") in ("True", "1", "true")
        contratos = self.search([("state", "=", "ativo")])
        geradas = 0
        for c in contratos:
            try:
                c.action_gerar_cobranca_competencia(competencia=comp, emitir=auto_emitir, enviar=auto_enviar)
                geradas += 1
            except Exception:
                continue
        return geradas
