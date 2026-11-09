from odoo import fields, models


class Condominio(models.Model):
    """Espelho dos condomínios cadastrados no IXC (o bridge sincroniza; IXC é a fonte)."""
    _name = "maisfibra.condominio"
    _description = "Condomínio (IXC)"
    _order = "name"

    name = fields.Char("Nome", required=True, index=True)
    ixc_id = fields.Char("ID no IXC", index=True, copy=False)
    endereco = fields.Char("Endereço")
    numero = fields.Char("Número")
    bairro = fields.Char("Bairro")
    cep = fields.Char("CEP")
    cidade = fields.Char("Cidade")
    ixc_cidade_id = fields.Char("ID cidade (IXC)")
    active = fields.Boolean(default=True)

    _sql_constraints = [("ixc_id_uniq", "unique(ixc_id)", "Condomínio já sincronizado.")]


class PlanoNegociacao(models.Model):
    """Planos de negociação do IXC (sincronizados pelo bridge)."""
    _name = "maisfibra.plano"
    _description = "Plano de negociação (IXC)"
    _order = "name"

    name = fields.Char("Plano", required=True)
    ixc_id = fields.Char("ID no IXC", index=True, copy=False)
    descricao = fields.Text("Descrição")
    active = fields.Boolean(default=True)

    _sql_constraints = [("ixc_id_uniq", "unique(ixc_id)", "Plano já sincronizado.")]


class CanalVenda(models.Model):
    _name = "maisfibra.canal.venda"
    _description = "Canal de venda"
    _order = "name"

    name = fields.Char("Canal", required=True)
    ixc_id = fields.Char("ID no IXC (se houver)")
    active = fields.Boolean(default=True)
