"""Consulta em tempo real de contratos e financeiro do cliente no IXC (via bridge).

O IXC continua sendo a fonte do financeiro; o Odoo só MOSTRA e guarda um resumo.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class IxcResumoMixin(models.AbstractModel):
    _name = "maisfibra.ixc.resumo.mixin"
    _description = "Resumo financeiro/serviço do IXC"

    mf_ixc_status_servico = fields.Char("Status do serviço (IXC)", readonly=True)
    mf_ixc_faturas_abertas = fields.Integer("Faturas em aberto", readonly=True)
    mf_ixc_faturas_vencidas = fields.Integer("Faturas vencidas", readonly=True)
    mf_ixc_valor_aberto = fields.Float("Valor em aberto (R$)", readonly=True, digits=(16, 2))
    mf_ixc_valor_vencido = fields.Float("Valor vencido (R$)", readonly=True, digits=(16, 2))
    mf_ixc_consultado_em = fields.Datetime("Consultado no IXC em", readonly=True)

    def _mf_ixc_chaves(self):
        """(id do cliente no IXC, CPF/CNPJ) — implementado em lead e contato."""
        raise NotImplementedError

    def action_mf_consultar_ixc(self):
        self.ensure_one()
        ixc_id, doc = self._mf_ixc_chaves()
        if not ixc_id and not doc:
            raise UserError(_("Sem ID do IXC nem CPF/CNPJ para consultar."))
        data = self.env["maisfibra.bridge"].call(
            "/odoo/cliente-ixc", {"ixc_cliente_id": ixc_id or "", "cpf_cnpj": doc or ""}
        )
        resumo = data.get("resumo") or {}
        vals = {
            "mf_ixc_status_servico": resumo.get("status_servico") or "",
            "mf_ixc_faturas_abertas": resumo.get("faturas_abertas") or 0,
            "mf_ixc_faturas_vencidas": resumo.get("faturas_vencidas") or 0,
            "mf_ixc_valor_aberto": resumo.get("valor_aberto") or 0.0,
            "mf_ixc_valor_vencido": resumo.get("valor_vencido") or 0.0,
            "mf_ixc_consultado_em": fields.Datetime.now(),
        }
        if not ixc_id and data.get("ixc_cliente_id") and "mf_ixc_cliente_id" in self._fields:
            vals["mf_ixc_cliente_id"] = data["ixc_cliente_id"]
        self.with_context(mf_from_bridge=True).sudo().write(vals)

        wizard = self.env["maisfibra.ixc.consulta"].create({
            "titulo": self.display_name,
            "ixc_cliente_id": data.get("ixc_cliente_id") or "",
            "status_servico": vals["mf_ixc_status_servico"],
            "valor_aberto": vals["mf_ixc_valor_aberto"],
            "valor_vencido": vals["mf_ixc_valor_vencido"],
            "contrato_ids": [(0, 0, {
                "ixc_id": c.get("ixc_id"), "nome": c.get("nome"), "status": c.get("status"),
                "status_internet": c.get("status_internet"),
                "data_ativacao": (c.get("data_ativacao") or "")[:10] or False,
            }) for c in data.get("contratos") or []],
            "fatura_ids": [(0, 0, {
                "ixc_id": f.get("ixc_id"),
                "vencimento": (f.get("vencimento") or "")[:10] or False,
                "valor": f.get("valor") or 0.0, "valor_aberto": f.get("valor_aberto") or 0.0,
                "status": f.get("status"), "vencida": f.get("vencida"),
                "linha_digitavel": f.get("linha_digitavel"),
            }) for f in data.get("faturas") or []],
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Cliente no IXC"),
            "res_model": "maisfibra.ixc.consulta",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }


class IxcConsulta(models.TransientModel):
    _name = "maisfibra.ixc.consulta"
    _description = "Consulta de cliente no IXC"

    titulo = fields.Char(readonly=True)
    ixc_cliente_id = fields.Char("ID cliente IXC", readonly=True)
    status_servico = fields.Char("Status do serviço", readonly=True)
    valor_aberto = fields.Float("Em aberto (R$)", readonly=True, digits=(16, 2))
    valor_vencido = fields.Float("Vencido (R$)", readonly=True, digits=(16, 2))
    contrato_ids = fields.One2many("maisfibra.ixc.consulta.contrato", "consulta_id", readonly=True)
    fatura_ids = fields.One2many("maisfibra.ixc.consulta.fatura", "consulta_id", readonly=True)


class IxcConsultaContrato(models.TransientModel):
    _name = "maisfibra.ixc.consulta.contrato"
    _description = "Contrato (IXC)"

    consulta_id = fields.Many2one("maisfibra.ixc.consulta", ondelete="cascade")
    ixc_id = fields.Char("ID")
    nome = fields.Char("Contrato")
    status = fields.Char("Status")
    status_internet = fields.Char("Internet")
    data_ativacao = fields.Date("Ativação")


class IxcConsultaFatura(models.TransientModel):
    _name = "maisfibra.ixc.consulta.fatura"
    _description = "Fatura (IXC)"
    _order = "vencimento desc"

    consulta_id = fields.Many2one("maisfibra.ixc.consulta", ondelete="cascade")
    ixc_id = fields.Char("ID")
    vencimento = fields.Date("Vencimento")
    valor = fields.Float("Valor", digits=(16, 2))
    valor_aberto = fields.Float("Em aberto", digits=(16, 2))
    status = fields.Char("Status")
    vencida = fields.Boolean("Vencida")
    linha_digitavel = fields.Char("Linha digitável")
