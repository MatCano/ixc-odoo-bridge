"""Controllers HTTP de Webhook externo e JSON-RPC para monitoramento/construtor."""
import hmac
import json
import logging

from odoo import _, http
from odoo.http import Response, request

_logger = logging.getLogger(__name__)


class AutomationController(http.Controller):
    @http.route("/maisfibra/automacao/webhook/<string:token>", type="http", auth="public", methods=["POST"], csrf=False)
    def trigger_webhook(self, token, **kwargs):
        Auto = request.env["mf.automation"].sudo()
        Exec = request.env["mf.automation.execution"].sudo()
        candidates = Auto.search([
            ("state", "=", "active"),
            ("trigger_type", "=", "webhook_received"),
            ("published_version_id", "!=", False),
        ])
        automation = Auto.browse()
        for cand in candidates:
            if cand.webhook_token and hmac.compare_digest(cand.webhook_token, token or ""):
                automation = cand
                break
        if not automation:
            return Response(json.dumps({"ok": False, "error": "unauthorized"}), status=401, content_type="application/json")

        try:
            payload = json.loads(request.httprequest.get_data() or b"{}")
        except ValueError:
            return Response(json.dumps({"ok": False, "error": "invalid json"}), status=400, content_type="application/json")

        model_name = automation.model_name
        Model = request.env[model_name].sudo()
        res_id = int(payload.get("record_id") or payload.get("res_id") or 0)
        record = Model.browse(res_id).exists() if res_id else Model.browse()
        if not record and payload.get("phone") and model_name == "crm.lead":
            tail = "".join(ch for ch in str(payload["phone"]) if ch.isdigit())[-8:]
            if tail:
                record = Model.search([("phone", "ilike", tail)], order="id desc", limit=1)
        if not record and payload.get("email"):
            email_field = "email_from" if model_name == "crm.lead" else "email"
            record = Model.search([(email_field, "=ilike", payload["email"])], order="id desc", limit=1)

        if not record and model_name == "crm.lead" and payload.get("create_if_missing"):
            vals = {
                "name": payload.get("name") or _("Lead Webhook (%s)", automation.name),
                "contact_name": payload.get("contact_name") or payload.get("name") or False,
                "email_from": payload.get("email") or False,
                "phone": payload.get("phone") or False,
                "type": "opportunity",
            }
            record = Model.create(vals)

        if not record:
            return Response(json.dumps({"ok": False, "error": "record not found"}), status=404, content_type="application/json")

        ext_event_id = str(
            payload.get("event_id") or request.httprequest.headers.get("X-Idempotency-Key") or ""
        ).strip()
        event_key = f"wh:{automation.id}:{ext_event_id}" if ext_event_id else ""

        if payload.get("event") == "reply_received":
            Exec.resume_waiting_on_event(model_name, record.id, "reply_received", extra_context={"payload": payload})

        execution = Exec.start_execution(automation, record, event_key=event_key, payload=payload)
        return Response(
            json.dumps({
                "ok": True,
                "automation_id": automation.id,
                "record_id": record.id,
                "execution_id": execution.id if execution else False,
                "state": execution.state if execution else "skipped",
            }),
            status=200,
            content_type="application/json",
        )

    @http.route("/maisfibra/automacao/monitor_data", type="jsonrpc", auth="user")
    def monitor_data(self, state_filter="all"):
        if not request.env.user.has_group("maisfibra_automacao.group_automacao_viewer"):
            return {"kpis": {}, "executions": []}
        return request.env["mf.automation.execution"].get_monitor_data(state_filter=state_filter)
