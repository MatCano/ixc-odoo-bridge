"""Emissão automática e idempotente de fatura ao classificar um Lead CRM como Ganho."""
import logging
import re
from datetime import date, timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class AccountMoveLeadLink(models.Model):
    _inherit = "account.move"

    mf_lead_id = fields.Many2one("crm.lead", string="Lead de Origem", index=True, copy=False, ondelete="set null")


class CrmLeadFatura(models.Model):
    _inherit = "crm.lead"

    mf_invoice_ids = fields.One2many("account.move", "mf_lead_id", string="Faturas Emitidas")
    mf_invoice_count = fields.Integer("Faturas", compute="_compute_mf_invoice_count")
    mf_cobranca_id = fields.Many2one(
        "maisfibra.cobranca", string="Cobrança / Boleto Gerado", copy=False, ondelete="set null"
    )

    @api.depends("mf_invoice_ids", "mf_invoice_ids.state")
    def _compute_mf_invoice_count(self):
        for lead in self:
            lead.mf_invoice_count = len(lead.mf_invoice_ids)

    def action_mf_view_invoices(self):
        self.ensure_one()
        action = {
            "type": "ir.actions.act_window",
            "name": _("Faturas do Lead"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("mf_lead_id", "=", self.id)],
            "context": {"default_move_type": "out_invoice", "default_mf_lead_id": self.id},
        }
        if len(self.mf_invoice_ids) == 1:
            action["view_mode"] = "form"
            action["res_id"] = self.mf_invoice_ids.id
        return action

    def _mf_garantir_parceiro_fatura(self):
        self.ensure_one()
        Partner = self.env["res.partner"].sudo()
        if self.partner_id:
            partner = self.partner_id.sudo()
            updates = {}
            if not partner.active:
                updates["active"] = True
                if "mf_whatsapp_sync" in partner._fields:
                    updates["mf_whatsapp_sync"] = False
            if not partner.email and self.email_from:
                updates["email"] = self.email_from
            if not partner.phone and self.phone:
                updates["phone"] = self.phone
            if not partner.vat and getattr(self, "mf_cpf_cnpj", False):
                updates["vat"] = self.mf_cpf_cnpj
            if updates:
                partner.write(updates)
            return partner

        base_domain = [("mf_whatsapp_sync", "=", False)] if "mf_whatsapp_sync" in Partner._fields else []
        partner = Partner
        if self.email_from:
            partner = Partner.search(base_domain + [("email", "=ilike", self.email_from)], limit=1)
        if not partner and self.phone:
            digits = re.sub(r"\D", "", self.phone)
            tail = digits[-8:] if len(digits) >= 8 else digits
            if tail:
                partner = Partner.search(base_domain + [("phone", "ilike", tail)], limit=1)
        if not partner:
            clean_name = (
                self.partner_name
                or self.contact_name
                or re.sub(r"^\[WhatsApp\]\s*", "", self.name or "").strip()
                or "Cliente CRM"
            )
            partner = Partner.create({
                "name": clean_name,
                "email": self.email_from or False,
                "phone": self.phone or False,
                "vat": getattr(self, "mf_cpf_cnpj", False) or False,
                "customer_rank": 1,
            })
        self.with_context(mf_skip_auto_invoice=True).sudo().write({"partner_id": partner.id})
        return partner

    def _mf_emitir_fatura_ganho(self):
        self.ensure_one()
        existentes = self.mf_invoice_ids.filtered(lambda m: m.state != "cancel")
        if existentes:
            return existentes[:1]

        partner = self._mf_garantir_parceiro_fatura()
        company = self.company_id or self.env.company
        Journal = self.env["account.journal"].sudo()
        journal = Journal.search(
            [("type", "=", "sale"), ("company_id", "=", company.id)],
            order="sequence asc, id asc",
            limit=1,
        ) or Journal.search([("type", "=", "sale")], order="sequence asc, id asc", limit=1)

        income_acc = journal.default_account_id if journal else False
        if not income_acc:
            income_acc = self.env["account.account"].sudo().search(
                [("account_type", "in", ("income", "income_other"))], limit=1
            )

        valor = float(self.expected_revenue or getattr(self, "recurring_revenue", 0.0) or 0.0)
        descricao = (
            getattr(self, "mf_descricao_produto", False)
            or (self.mf_plano_id.name if getattr(self, "mf_plano_id", False) else "")
            or self.name
            or "Serviço contratado"
        )
        hoje = date.today()
        vencimento = hoje + timedelta(days=5)

        line_vals = {
            "name": descricao,
            "quantity": 1.0,
            "price_unit": valor,
        }
        if income_acc:
            line_vals["account_id"] = income_acc.id

        move_vals = {
            "move_type": "out_invoice",
            "partner_id": partner.id,
            "company_id": company.id,
            "invoice_date": hoje,
            "invoice_date_due": vencimento,
            "invoice_origin": self.name or f"Lead #{self.id}",
            "ref": f"LEAD-{self.id}",
            "mf_lead_id": self.id,
            "invoice_line_ids": [(0, 0, line_vals)],
        }
        if journal:
            move_vals["journal_id"] = journal.id

        move = self.env["account.move"].sudo().create(move_vals)
        if getattr(move, "l10n_latam_use_documents", False):
            doc_updates = {}
            if not move.l10n_latam_document_type_id:
                doc_type = self.env["l10n_latam.document.type"].sudo().search(
                    [("internal_type", "in", ("invoice", "all"))], limit=1
                )
                if doc_type:
                    doc_updates["l10n_latam_document_type_id"] = doc_type.id
            if doc_updates:
                move.write(doc_updates)
            if getattr(move, "l10n_latam_manual_document_number", False) and not move.l10n_latam_document_number:
                move.write({"l10n_latam_document_number": str(move.id).zfill(8)})

        move.action_post()

        if valor > 0 and not self.mf_cobranca_id:
            try:
                cob = self.env["maisfibra.cobranca"].sudo().create({
                    "partner_id": partner.id,
                    "company_id": company.id,
                    "competencia": hoje.strftime("%Y-%m"),
                    "descricao": descricao,
                    "data_emissao": hoje,
                    "data_vencimento": vencimento,
                    "valor_original": valor,
                    "move_id": move.id,
                    "chave_idempotencia": f"lead|{self.id}|{move.id}",
                })
                cob.action_emitir_boleto()
                self.with_context(mf_skip_auto_invoice=True).sudo().write({"mf_cobranca_id": cob.id})
            except Exception as err:  # noqa: BLE001 — cobrança auxiliar nunca impede a emissão da fatura
                _logger.warning("Lead %s: falha ao gerar cobrança vinculada à fatura %s: %s", self.id, move.name, err)

        self.invalidate_recordset(["mf_invoice_ids", "mf_invoice_count"])
        self.message_post(
            body=_("🧾 Fatura <b>%(num)s</b> emitida automaticamente no valor de R$ %(val).2f.", num=move.name, val=move.amount_total),
            subtype_xmlid="mail.mt_note",
        )
        return move

    def write(self, vals):
        prev_won_ids = set()
        if "stage_id" in vals:
            prev_won_ids = set(self.filtered(lambda l: l.stage_id.is_won).ids)
        res = super().write(vals)
        if "stage_id" in vals and not self.env.context.get("mf_skip_auto_invoice"):
            target_stage = self.env["crm.stage"].browse(vals["stage_id"])
            if target_stage.is_won:
                for lead in self:
                    if lead.id not in prev_won_ids:
                        lead._mf_emitir_fatura_ganho()
        return res
