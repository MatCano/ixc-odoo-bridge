from odoo import fields, models

STAGE_CODES = [
    ("recolher_docs", "Recolher Documentos (vira prospect no IXC)"),
    ("negociacao", "Negociação (cria negociação no IXC)"),
    ("aguardando_assinatura", "Aguardar Assinatura (ganho no IXC)"),
    ("perdemos", "Perdemos (perda no IXC)"),
]


class CrmStage(models.Model):
    _inherit = "crm.stage"

    # As regras usam o CÓDIGO, não o nome do estágio: renomear não quebra nada,
    # e cada funil (B2B/B2C/Retenção) pode ter seu próprio estágio com o mesmo código.
    mf_code = fields.Selection(STAGE_CODES, string="Regra MaisFibra", copy=False,
                               help="Define qual automação do IXC roda quando o lead entra neste estágio.")
