"""Registro auditável de disparos de boletos e documentos fiscais (e-mail e WhatsApp)."""
from odoo import fields, models


class CobrancaEnvio(models.Model):
    _name = "maisfibra.cobranca.envio"
    _description = "Histórico de Envio de Boletos e Notas Fiscais"
    _order = "data_envio desc, id desc"

    cobranca_id = fields.Many2one("maisfibra.cobranca", "Cobrança", ondelete="cascade", index=True)
    nota_fiscal_id = fields.Many2one("maisfibra.nota.fiscal", "Nota Fiscal", ondelete="cascade", index=True)
    partner_id = fields.Many2one("res.partner", "Cliente", required=True, ondelete="cascade", index=True)
    data_envio = fields.Datetime("Data/Hora do Envio", default=fields.Datetime.now, required=True, readonly=True)
    canal = fields.Selection(
        [("email", "E-mail"), ("whatsapp", "WhatsApp")],
        string="Canal", required=True, readonly=True, index=True,
    )
    tipo_documento = fields.Selection(
        [("boleto", "Boleto"), ("nota_fiscal", "Nota Fiscal"), ("ambos", "Boleto + NF")],
        string="Documento", default="boleto", required=True, readonly=True,
    )
    tipo_disparo = fields.Selection(
        [("automatico", "Automático"), ("manual_reenvio", "Reenvio Manual")],
        string="Tipo de Disparo", default="automatico", required=True, readonly=True,
    )
    destinatario = fields.Char("Destinatário", readonly=True)
    status = fields.Selection(
        [("sucesso", "Enviado com sucesso"), ("falha", "Falha no envio"), ("ignorado", "Ignorado (regra/consentimento)")],
        string="Resultado", required=True, readonly=True, index=True,
    )
    mensagem_erro = fields.Text("Detalhe / Erro", readonly=True)
    external_id = fields.Char("ID Externo (Provedor)", readonly=True)
    user_id = fields.Many2one("res.users", "Usuário Responsável", default=lambda self: self.env.uid, readonly=True)
