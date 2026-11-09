import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Campos que não interessam à auditoria (técnicos, calculados ou de chatter)
IGNORAR = {
    "write_date", "write_uid", "create_date", "create_uid", "__last_update",
    "message_ids", "message_follower_ids", "activity_ids", "website_message_ids",
    "message_main_attachment_id", "message_partner_ids", "rating_ids",
}
RETENCAO_MESES = 13  # contrato pede 12 meses; guarda 1 mês de folga


class AuditLog(models.Model):
    _name = "maisfibra.audit.log"
    _description = "Registro de auditoria"
    _order = "id desc"
    _log_access = True

    data = fields.Datetime("Quando", default=fields.Datetime.now, readonly=True, index=True)
    user_id = fields.Many2one("res.users", "Quem", readonly=True, index=True, ondelete="set null")
    model = fields.Char("Modelo", readonly=True, index=True)
    model_desc = fields.Char("Tipo de registro", readonly=True)
    res_id = fields.Integer("ID do registro", readonly=True, index=True)
    res_name = fields.Char("Registro", readonly=True)
    operacao = fields.Selection(
        [("create", "Criação"), ("write", "Alteração"), ("unlink", "Exclusão")],
        string="Operação", readonly=True, index=True,
    )
    linha_ids = fields.One2many("maisfibra.audit.log.linha", "log_id", "Campos alterados", readonly=True)
    origem = fields.Char("Origem", readonly=True,
                         help="'bridge' quando a alteração veio do sincronismo com o IXC.")

    def write(self, vals):
        raise UserError(_("Registros de auditoria não podem ser alterados."))

    def unlink(self):
        if not self.env.context.get("mf_audit_purge"):
            raise UserError(_("Registros de auditoria não podem ser excluídos."))
        return super().unlink()

    def action_abrir_registro(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": self.model,
                "res_id": self.res_id, "view_mode": "form", "target": "current"}

    @api.model
    def _cron_retencao(self):
        limite = fields.Datetime.now() - timedelta(days=30 * RETENCAO_MESES)
        antigos = self.sudo().search([("data", "<", limite)], limit=50000)
        if antigos:
            antigos.with_context(mf_audit_purge=True).unlink()
            _logger.info("Auditoria: %s registros com mais de %s meses removidos", len(antigos), RETENCAO_MESES)


class AuditLogLinha(models.Model):
    _name = "maisfibra.audit.log.linha"
    _description = "Campo alterado"

    log_id = fields.Many2one("maisfibra.audit.log", required=True, ondelete="cascade", index=True)
    campo = fields.Char("Campo técnico", readonly=True)
    campo_desc = fields.Char("Campo", readonly=True)
    antes = fields.Text("Antes", readonly=True)
    depois = fields.Text("Depois", readonly=True)

    def write(self, vals):
        raise UserError(_("Registros de auditoria não podem ser alterados."))
