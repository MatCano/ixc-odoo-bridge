from odoo import fields, models


class ResPartner(models.Model):
    _name = "res.partner"
    _inherit = ["res.partner", "maisfibra.endereco.mixin"]

    mf_ixc_contato_id = fields.Char("ID Lead (IXC)", index=True, copy=False)
    mf_ixc_cliente_id = fields.Char("ID Cliente (IXC)", index=True, copy=False)
