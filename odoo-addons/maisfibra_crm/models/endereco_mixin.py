from odoo import api, fields, models

# Campos do endereço de INSTALAÇÃO, separados do endereço padrão do Odoo
# (que fica como endereço de cobrança/cadastro). Mesmo conjunto em Contato e Lead.
ENDERECO_FIELDS = [
    "mf_condominio_id", "mf_bloco", "mf_apartamento", "mf_cep", "mf_endereco",
    "mf_numero", "mf_complemento", "mf_bairro", "mf_cidade",
]


class EnderecoInstalacaoMixin(models.AbstractModel):
    _name = "maisfibra.endereco.mixin"
    _description = "Endereço de instalação MaisFibra"

    mf_condominio_id = fields.Many2one("maisfibra.condominio", string="Condomínio", tracking=True)
    mf_bloco = fields.Char("Bloco")
    mf_apartamento = fields.Char("Apartamento")
    mf_cep = fields.Char("CEP (instalação)")
    mf_endereco = fields.Char("Endereço (instalação)")
    mf_numero = fields.Char("Número")
    mf_complemento = fields.Char("Complemento")
    mf_bairro = fields.Char("Bairro")
    mf_cidade = fields.Char("Cidade (instalação)")

    @api.onchange("mf_condominio_id")
    def _onchange_mf_condominio(self):
        c = self.mf_condominio_id
        if c:
            self.mf_endereco = c.endereco or self.mf_endereco
            self.mf_numero = c.numero or self.mf_numero
            self.mf_bairro = c.bairro or self.mf_bairro
            self.mf_cep = c.cep or self.mf_cep
            self.mf_cidade = c.cidade or self.mf_cidade
