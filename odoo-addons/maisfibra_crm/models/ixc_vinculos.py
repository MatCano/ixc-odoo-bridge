"""Vínculo de vendedores e funis do Odoo com os IDs do IXC (validado 09/11: cliente.id_vendedor,
crm_negociacoes.id_funil; funis do IXC: CLIENTE PF, CLIENTE PJ, RENOVAÇÃO, Novos leads)."""
from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    mf_ixc_vendedor_id = fields.Char(
        "ID do vendedor no IXC",
        help="Cadastro de vendedores do IXC. Vai em id_vendedor do prospect criado por este usuário.",
    )


class CrmTeam(models.Model):
    _inherit = "crm.team"

    mf_ixc_funil_id = fields.Char(
        "ID do funil no IXC",
        help="Funil do CRM do IXC (crm_funil) usado nas negociações deste funil. Vazio = padrão do bridge (7).",
    )
