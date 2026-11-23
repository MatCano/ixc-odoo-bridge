import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .endereco_mixin import ENDERECO_FIELDS

# Obrigatórios por etapa (pedido do cliente)
REQ_RECOLHER_DOCS = {
    "email_from": "E-mail",
    "mf_cpf_cnpj": "CPF/CNPJ",
    "mf_condominio_id": "Condomínio",
    "mf_bloco": "Bloco",
    "mf_apartamento": "Apartamento",
    "mf_cep": "CEP",
    "mf_endereco": "Endereço",
    "mf_cidade": "Cidade",
}
REQ_NEGOCIACAO = {
    "mf_cidade": "Cidade",
    "mf_cpf_cnpj": "CPF/CNPJ",
    "email_from": "E-mail",
    "mf_plano_id": "Plano de negociação",
    "mf_descricao_produto": "Descrição do produto",
}


def _digits(v):
    return re.sub(r"\D", "", v or "")


class CrmLead(models.Model):
    _name = "crm.lead"
    _inherit = ["crm.lead", "maisfibra.endereco.mixin", "maisfibra.ixc.resumo.mixin"]

    # --- Vínculos com o IXC (substituem os marcadores em texto da descrição) ---
    mf_ixc_contato_id = fields.Char("ID Lead (IXC)", index=True, copy=False, tracking=True)
    mf_ixc_cliente_id = fields.Char("ID Prospect/Cliente (IXC)", index=True, copy=False, tracking=True)
    mf_ixc_negociacao_id = fields.Char("ID Negociação (IXC)", index=True, copy=False, tracking=True)

    # --- Dados comerciais ---
    mf_cpf_cnpj = fields.Char("CPF/CNPJ", index=True, tracking=True)
    mf_origem = fields.Char("Como nos conheceu", tracking=True,
                            help="Vem da mensagem do OPA/Chatwoot/WhatsApp.")
    mf_canal_venda_id = fields.Many2one("maisfibra.canal.venda", string="Canal de venda", tracking=True)
    mf_indicado_por_id = fields.Many2one("res.partner", string="Indicado por")
    mf_concorrente = fields.Char("Concorrente (de onde veio)")

    # --- Negociação ---
    mf_plano_id = fields.Many2one("maisfibra.plano", string="Plano de negociação", tracking=True)
    mf_descricao_produto = fields.Text("Descrição do produto")

    # --- Crédito ---
    mf_credito_status = fields.Selection(
        [("nao_consultado", "Não consultado"), ("aprovado", "Aprovado"),
         ("reprovado", "Reprovado"), ("pendente", "Pendente")],
        string="Análise de crédito", default="nao_consultado", tracking=True,
    )
    mf_credito_obs = fields.Text("Retorno da análise")

    mf_stage_code = fields.Selection(related="stage_id.mf_code", string="Regra do estágio")

    # --- Navegação sequencial de etapas (Back / Next) ---
    mf_prev_stage_id = fields.Many2one("crm.stage", string="Etapa anterior", compute="_compute_mf_stage_nav")
    mf_next_stage_id = fields.Many2one("crm.stage", string="Próxima etapa", compute="_compute_mf_stage_nav")
    mf_can_stage_back = fields.Boolean(compute="_compute_mf_stage_nav")
    mf_can_stage_next = fields.Boolean(compute="_compute_mf_stage_nav")
    mf_next_stage_is_won = fields.Boolean(compute="_compute_mf_stage_nav")
    mf_stage_name = fields.Char(related="stage_id.name", string="Nome da etapa atual", readonly=True)
    mf_next_stage_name = fields.Char(related="mf_next_stage_id.name", string="Nome da próxima etapa", readonly=True)

    def _mf_get_pipeline_stages(self):
        self.ensure_one()
        Stage = self.env["crm.stage"]
        domain = []
        if "team_ids" in Stage._fields:
            if self.team_id:
                domain = ["|", ("team_ids", "in", self.team_id.ids), ("team_ids", "=", False)]
            else:
                domain = [("team_ids", "=", False)]
        elif "team_id" in Stage._fields:
            domain = ["|", ("team_id", "=", self.team_id.id), ("team_id", "=", False)]
        stages = Stage.search(domain, order="sequence asc, id asc")
        if not stages:
            stages = Stage.search([], order="sequence asc, id asc")
        return stages.filtered(
            lambda s: (not s.fold or s.id == self.stage_id.id) and s.mf_code != "perdemos"
        )

    @api.depends("stage_id", "team_id", "type", "active")
    def _compute_mf_stage_nav(self):
        for lead in self:
            if lead.type != "opportunity" or not lead.active:
                lead.mf_prev_stage_id = False
                lead.mf_next_stage_id = False
                lead.mf_can_stage_back = False
                lead.mf_can_stage_next = False
                lead.mf_next_stage_is_won = False
                continue
            stages = list(lead._mf_get_pipeline_stages())
            prev_stage = False
            next_stage = False
            if lead.stage_id and lead.stage_id in stages:
                idx = stages.index(lead.stage_id)
                if idx > 0:
                    prev_stage = stages[idx - 1]
                if idx + 1 < len(stages):
                    next_stage = stages[idx + 1]
            elif stages:
                next_stage = stages[0]
            lead.mf_prev_stage_id = prev_stage
            lead.mf_next_stage_id = next_stage
            lead.mf_can_stage_back = bool(prev_stage)
            lead.mf_can_stage_next = bool(next_stage)
            lead.mf_next_stage_is_won = bool(next_stage and next_stage.is_won)

    def action_mf_stage_back(self):
        self.ensure_one()
        if not self.mf_prev_stage_id:
            raise UserError(_("Este lead já está na primeira etapa do funil."))
        was_won = bool(self.stage_id.is_won)
        self.write({"stage_id": self.mf_prev_stage_id.id})
        if was_won:
            self._compute_probabilities()
            prob = self.automated_probability if (self.automated_probability or 0.0) < 100.0 else 50.0
            self.write({"probability": prob})
        return True

    def action_mf_stage_next(self):
        self.ensure_one()
        if not self.mf_next_stage_id:
            raise UserError(_("Este lead já está na última etapa do funil."))
        target = self.mf_next_stage_id
        vals = {"stage_id": target.id}
        if target.is_won:
            vals["probability"] = 100
        self.write(vals)
        return True

    # ------------------------------------------------------------------
    # Copia CPF/endereço do contato ao escolher o cliente
    # ------------------------------------------------------------------
    @api.onchange("partner_id")
    def _onchange_partner_mf(self):
        p = self.partner_id
        if not p:
            return
        if not self.mf_cpf_cnpj and p.vat:
            self.mf_cpf_cnpj = p.vat
        for f in ENDERECO_FIELDS:
            if not self[f] and p[f]:
                self[f] = p[f]

    # ------------------------------------------------------------------
    # Regras de mudança de estágio
    # ------------------------------------------------------------------
    def write(self, vals):
        res = super().write(vals)
        if "stage_id" in vals and not self.env.context.get("mf_from_bridge"):
            code = self.env["crm.stage"].browse(vals["stage_id"]).mf_code
            for lead in self:
                if code == "recolher_docs":
                    lead._mf_virar_prospect()
                elif code == "negociacao":
                    lead._mf_criar_negociacao()
        return res

    def _mf_check_required(self, required, etapa):
        faltando = [label for field, label in required.items() if not self[field]]
        if faltando:
            raise UserError(_(
                "Para mover \"%(lead)s\" para %(etapa)s, preencha: %(campos)s.",
                lead=self.name, etapa=etapa, campos=", ".join(faltando),
            ))
        if len(_digits(self.mf_cpf_cnpj)) not in (11, 14):
            raise UserError(_("CPF/CNPJ inválido em \"%s\".", self.name))

    def _mf_payload(self):
        self.ensure_one()
        c = self.mf_condominio_id
        return {
            "odoo_lead_id": self.id,
            "ixc_contato_id": self.mf_ixc_contato_id or "",
            "ixc_cliente_id": self.mf_ixc_cliente_id or "",
            "nome": self.partner_id.name or self.contact_name or self.name,
            "cpf_cnpj": _digits(self.mf_cpf_cnpj),
            "email": self.email_from or "",
            "telefone": self.phone or "",
            "origem": self.mf_origem or "",
            "canal_venda": self.mf_canal_venda_id.name or "",
            "canal_venda_ixc_id": self.mf_canal_venda_id.ixc_id or "",
            "indicado_por": self.mf_indicado_por_id.name or "",
            "concorrente": self.mf_concorrente or "",
            "vendedor": self.user_id.name or "",
            "vendedor_ixc_id": self.user_id.mf_ixc_vendedor_id or "",
            "funil_ixc_id": self.team_id.mf_ixc_funil_id or "",
            "condominio_ixc_id": c.ixc_id or "",
            "condominio": c.name or "",
            "bloco": self.mf_bloco or "",
            "apartamento": self.mf_apartamento or "",
            "cep": _digits(self.mf_cep),
            "endereco": self.mf_endereco or "",
            "numero": self.mf_numero or "",
            "complemento": self.mf_complemento or "",
            "bairro": self.mf_bairro or "",
            "cidade": self.mf_cidade or "",
            "cidade_ixc_id": c.ixc_cidade_id or "",
        }

    def _mf_virar_prospect(self):
        self.ensure_one()
        self._mf_check_required(REQ_RECOLHER_DOCS, "Recolher Documentos")
        data = self.env["maisfibra.bridge"].call("/odoo/prospect", self._mf_payload())
        vals = {"mf_ixc_cliente_id": str(data.get("ixc_cliente_id") or self.mf_ixc_cliente_id or "")}
        self.with_context(mf_from_bridge=True).sudo().write(vals)
        self.message_post(body=_("✅ Prospect salvo no IXC (ID %s).", vals["mf_ixc_cliente_id"]),
                          subtype_xmlid="mail.mt_note")

    def _mf_criar_negociacao(self):
        self.ensure_one()
        self._mf_check_required(REQ_NEGOCIACAO, "Negociação")
        if not self.mf_ixc_cliente_id:
            raise UserError(_("\"%s\" ainda não é prospect no IXC. Passe antes por Recolher Documentos.", self.name))
        payload = self._mf_payload()
        payload.update({
            "plano_ixc_id": self.mf_plano_id.ixc_id or "",
            "plano": self.mf_plano_id.name,
            "descricao_produto": self.mf_descricao_produto,
            "ixc_negociacao_id": self.mf_ixc_negociacao_id or "",
            "valor_esperado": self.expected_revenue or 0,
        })
        data = self.env["maisfibra.bridge"].call("/odoo/negociacao", payload)
        neg_id = str(data.get("ixc_negociacao_id") or "")
        self.with_context(mf_from_bridge=True).sudo().write({"mf_ixc_negociacao_id": neg_id})
        self.message_post(body=_("✅ Negociação criada no IXC (ID %s).", neg_id),
                          subtype_xmlid="mail.mt_note")

    def _mf_ixc_chaves(self):
        self.ensure_one()
        return (self.mf_ixc_cliente_id or self.partner_id.mf_ixc_cliente_id,
                _digits(self.mf_cpf_cnpj or self.partner_id.vat))

    def action_mf_consultar_credito(self):
        self.ensure_one()
        if not self.mf_cpf_cnpj:
            raise UserError(_("Informe o CPF/CNPJ para consultar crédito."))
        data = self.env["maisfibra.bridge"].call("/odoo/consulta-credito", self._mf_payload())
        status = data.get("status")
        self.write({
            "mf_credito_status": status if status in ("aprovado", "reprovado", "pendente") else "pendente",
            "mf_credito_obs": data.get("mensagem") or "",
        })
        return True
