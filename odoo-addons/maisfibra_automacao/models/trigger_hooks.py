"""Hooks leves orientados a eventos no CRM, Contatos, Atividades e WhatsApp."""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class CrmLeadAutomationHook(models.Model):
    _inherit = "crm.lead"

    def _mf_dispatch_automations(self, trigger_types, vals_by_id=None):
        """Dispara automações ativas compatíveis com proteção anti-loop."""
        ctx = self.env.context
        depth = int(ctx.get("mf_automation_depth") or 0)
        source_auto_id = ctx.get("mf_automation_source_id")
        Auto = self.env["mf.automation"].sudo()
        Exec = self.env["mf.automation.execution"].sudo()

        autos = Auto.search([
            ("state", "=", "active"),
            ("model_name", "=", "crm.lead"),
            ("trigger_type", "in", list(trigger_types)),
            ("published_version_id", "!=", False),
        ])
        if not autos:
            return
        for lead in self:
            info = (vals_by_id or {}).get(lead.id, {})
            for auto in autos:
                if ctx.get("mf_automation_engine") and source_auto_id == auto.id:
                    continue
                if auto.trigger_type == "lead_assigned" and not lead.user_id:
                    continue
                if auto.trigger_type == "stage_entered" and auto.trigger_stage_id and lead.stage_id.id != auto.trigger_stage_id.id:
                    continue
                if auto.trigger_type == "stage_exited" and auto.trigger_stage_id and info.get("old_stage_id") != auto.trigger_stage_id.id:
                    continue
                if auto.trigger_type == "field_changed" and auto.trigger_field_name and auto.trigger_field_name not in info.get("changed_fields", set()):
                    continue
                if auto.trigger_type == "lead_won_or_lost":
                    won_state = info.get("won_lost_state")
                    if auto.trigger_won_lost == "won" and won_state != "won":
                        continue
                    if auto.trigger_won_lost == "lost" and won_state != "lost":
                        continue
                if auto.trigger_pipeline_id and lead.team_id.id != auto.trigger_pipeline_id.id:
                    continue
                ev_key = info.get("event_key") or ""
                Exec.start_execution(auto, lead, event_key=ev_key, depth=depth)

    @api.model_create_multi
    def create(self, vals_list):
        leads = super().create(vals_list)
        if self.env.context.get("mf_automation_skip"):
            return leads
        triggers = {"lead_created"}
        for lead in leads:
            if lead.user_id:
                triggers.add("lead_assigned")
        vals_by_id = {
            lead.id: {"event_key": f"lead_create:{lead.id}"}
            for lead in leads
        }
        leads._mf_dispatch_automations(triggers, vals_by_id)
        return leads

    def write(self, vals):
        before = {}
        for lead in self:
            before[lead.id] = {
                "stage_id": lead.stage_id.id,
                "user_id": lead.user_id.id,
                "active": lead.active,
                "won_status": getattr(lead, "won_status", "pending"),
            }
        res = super().write(vals)
        if self.env.context.get("mf_automation_skip"):
            return res

        changed_fields = set(vals.keys())
        Exec = self.env["mf.automation.execution"].sudo()
        for lead in self:
            prev = before.get(lead.id, {})
            triggers = {"field_changed"} if changed_fields else set()
            info = {"changed_fields": changed_fields, "old_stage_id": prev.get("stage_id")}

            if "user_id" in vals and vals.get("user_id") != prev.get("user_id"):
                triggers.add("owner_changed")
                if not prev.get("user_id") and vals.get("user_id"):
                    triggers.add("lead_assigned")

            if "stage_id" in vals and vals.get("stage_id") != prev.get("stage_id"):
                triggers.update({"stage_changed", "stage_entered", "stage_exited", "opportunity_status_changed"})
                Exec.resume_waiting_on_event("crm.lead", lead.id, "stage_changed")
                if lead.stage_id.is_won:
                    triggers.add("lead_won_or_lost")
                    info["won_lost_state"] = "won"
                    Exec.resume_waiting_on_event("crm.lead", lead.id, "won")

            if ("active" in vals and not lead.active) or (getattr(lead, "won_status", "") == "lost" and prev.get("won_status") != "lost"):
                triggers.update({"lead_won_or_lost", "opportunity_status_changed"})
                info["won_lost_state"] = "lost"
                Exec.resume_waiting_on_event("crm.lead", lead.id, "lost")

            lead._mf_dispatch_automations(triggers, {lead.id: info})
        return res


class ResPartnerAutomationHook(models.Model):
    _inherit = "res.partner"

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        if self.env.context.get("mf_automation_skip"):
            return partners
        Auto = self.env["mf.automation"].sudo()
        Exec = self.env["mf.automation.execution"].sudo()
        autos = Auto.search([
            ("state", "=", "active"),
            ("model_name", "=", "res.partner"),
            ("trigger_type", "=", "partner_created"),
            ("published_version_id", "!=", False),
        ])
        for partner in partners:
            if "mf_whatsapp_sync" in partner._fields and partner.mf_whatsapp_sync:
                continue
            for auto in autos:
                Exec.start_execution(auto, partner, event_key=f"partner_create:{auto.id}:{partner.id}")
        return partners

    def write(self, vals):
        res = super().write(vals)
        if self.env.context.get("mf_automation_skip") or not vals:
            return res
        changed = set(vals.keys())
        Auto = self.env["mf.automation"].sudo()
        Exec = self.env["mf.automation.execution"].sudo()
        autos = Auto.search([
            ("state", "=", "active"),
            ("model_name", "=", "res.partner"),
            ("trigger_type", "=", "field_changed"),
            ("published_version_id", "!=", False),
        ])
        for partner in self:
            for auto in autos:
                if auto.trigger_field_name and auto.trigger_field_name not in changed:
                    continue
                if self.env.context.get("mf_automation_engine") and self.env.context.get("mf_automation_source_id") == auto.id:
                    continue
                Exec.start_execution(auto, partner)
        return res


class MailActivityAutomationHook(models.Model):
    _inherit = "mail.activity"

    def _mf_notify_activity(self, event_state):
        Auto = self.env["mf.automation"].sudo()
        Exec = self.env["mf.automation.execution"].sudo()
        for act in self:
            if act.res_model not in ("crm.lead", "res.partner") or not act.res_id:
                continue
            record = self.env[act.res_model].sudo().browse(act.res_id).exists()
            if not record:
                continue
            if event_state == "done":
                Exec.resume_waiting_on_event(act.res_model, act.res_id, "activity_done")
            autos = Auto.search([
                ("state", "=", "active"),
                ("model_name", "=", act.res_model),
                ("trigger_type", "=", "activity_event"),
                ("trigger_activity_state", "=", event_state),
                ("published_version_id", "!=", False),
            ])
            for auto in autos:
                if self.env.context.get("mf_automation_engine") and self.env.context.get("mf_automation_source_id") == auto.id:
                    continue
                Exec.start_execution(auto, record, event_key=f"act_{event_state}:{auto.id}:{act.id}")

    @api.model_create_multi
    def create(self, vals_list):
        acts = super().create(vals_list)
        acts._mf_notify_activity("created")
        return acts

    def _action_done(self, feedback=False, attachment_ids=None):
        snapshot = self.sudo()
        snapshot._mf_notify_activity("done")
        return super()._action_done(feedback=feedback, attachment_ids=attachment_ids)


class WhatsappInboundAutomationHook(models.Model):
    _inherit = "maisfibra.whatsapp.message"

    @api.model_create_multi
    def create(self, vals_list):
        msgs = super().create(vals_list)
        Exec = self.env["mf.automation.execution"].sudo()
        for msg in msgs:
            if msg.direction == "in":
                if msg.lead_id:
                    Exec.resume_waiting_on_event("crm.lead", msg.lead_id.id, "reply_received")
                if msg.partner_id:
                    Exec.resume_waiting_on_event("res.partner", msg.partner_id.id, "reply_received")
        return msgs
