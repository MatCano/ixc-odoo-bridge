from odoo import fields, models

from .crm_lead import _digits


class ResPartner(models.Model):
    _name = "res.partner"
    _inherit = ["res.partner", "maisfibra.endereco.mixin", "maisfibra.ixc.resumo.mixin"]

    mf_ixc_contato_id = fields.Char("ID Lead (IXC)", index=True, copy=False)
    mf_ixc_cliente_id = fields.Char("ID Cliente (IXC)", index=True, copy=False)

    def _mf_ixc_chaves(self):
        self.ensure_one()
        return self.mf_ixc_cliente_id, _digits(self.vat)
