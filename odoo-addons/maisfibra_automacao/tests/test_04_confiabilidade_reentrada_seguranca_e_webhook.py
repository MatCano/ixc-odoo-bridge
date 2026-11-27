import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestConfiabilidadeSegurancaEWebhook(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mf_audit_off=True))
        cls.partner = cls.env["res.partner"].create({
            "name": "Cliente Confiabilidade TDD",
            "email": "confia.sigiloso@maisfibra.local",
            "phone": "5511944443333",
        })
        cls.stage_won = cls.env["crm.stage"].sudo().search([("is_won", "=", True)], limit=1)

    def test_01_retentativa_backoff_e_reprocessamento_manual(self):
        auto = self.env["mf.automation"].create({
            "name": "Fluxo Retry TDD",
            "model_name": "crm.lead",
            "trigger_type": "webhook_received",
        })
        auto.save_builder_graph({
            "nodes": [
                {"node_key": "t1", "name": "Entrada", "category": "trigger", "node_type": "trigger_entry", "config": {}},
                {"node_key": "wh1", "name": "Webhook Externo", "category": "integration", "node_type": "call_webhook", "config": {"url": "https://api.exemplo.local/hook"}},
            ],
            "edges": [{"source_key": "t1", "target_key": "wh1", "branch": "default"}],
        })
        auto.action_publish()
        lead = self.env["crm.lead"].create({"name": "Lead Retry", "partner_id": self.partner.id, "type": "opportunity"})

        import requests
        with patch("odoo.addons.maisfibra_automacao.models.automation_action.requests.post", side_effect=requests.Timeout("timeout")):
            ex = self.env["mf.automation.execution"].start_execution(auto, lead, event_key="retry:1")

        self.assertEqual(ex.state, "queued")
        self.assertEqual(ex.retry_count, 1)
        self.assertTrue(ex.next_run_at > fields.Datetime.now())

        # Esgota tentativas -> failed
        ex.sudo().write({"retry_count": 3, "next_run_at": fields.Datetime.now() - timedelta(seconds=1)})
        with patch("odoo.addons.maisfibra_automacao.models.automation_action.requests.post", side_effect=requests.Timeout("timeout")):
            self.env["mf.automation.execution"]._cron_process_queue()
        ex.invalidate_recordset()
        self.assertEqual(ex.state, "failed")

        # Reprocessamento autorizado funciona
        class _Resp:
            status_code = 200
            text = "ok"

        with patch("odoo.addons.maisfibra_automacao.models.automation_action.requests.post", return_value=_Resp()):
            ex.action_retry_execution()
        ex.invalidate_recordset()
        self.assertEqual(ex.state, "done")

    def test_02_reentrada_saida_antecipada_e_sanitizacao_pii(self):
        auto = self.env["mf.automation"].create({
            "name": "Fluxo Saída Ganho",
            "model_name": "crm.lead",
            "trigger_type": "webhook_received",
            "reentry_policy": "after_completion",
            "exit_event": "won",
            "allow_reentry_after_exit": False,
        })
        auto.save_builder_graph({
            "nodes": [
                {"node_key": "t1", "name": "Entrada", "category": "trigger", "node_type": "trigger_entry", "config": {}},
                {"node_key": "w1", "name": "Esperar", "category": "wait", "node_type": "wait_duration", "config": {"amount": 2, "unit": "days"}},
            ],
            "edges": [{"source_key": "t1", "target_key": "w1", "branch": "default"}],
        })
        auto.action_publish()
        lead = self.env["crm.lead"].create({"name": "Lead Exit TDD", "partner_id": self.partner.id, "type": "opportunity"})

        ex = self.env["mf.automation.execution"].start_execution(auto, lead, payload={"cpf": "123.456.789-00", "api_token": "segredo123"})
        self.assertEqual(ex.state, "waiting")
        self.assertIn("***REDACTED***", ex.payload_json)
        self.assertNotIn("123.456.789-00", ex.payload_json)

        # Bloqueia simultânea
        ex2 = self.env["mf.automation.execution"].start_execution(auto, lead)
        self.assertFalse(ex2)

        # Ganho cancela execução em espera e bloqueia reentrada após saída
        if self.stage_won:
            lead.write({"stage_id": self.stage_won.id})
            ex.invalidate_recordset()
            self.assertEqual(ex.state, "cancelled")
            ex3 = self.env["mf.automation.execution"].start_execution(auto, lead)
            self.assertFalse(ex3)

    def test_03_permissoes_e_webhook_http_idempotente(self):
        viewer = self.env["res.users"].create({
            "name": "Viewer Auto TDD",
            "login": "viewer_auto_tdd",
            "email": "viewer@maisfibra.local",
            "group_ids": [(6, 0, [self.env.ref("maisfibra_automacao.group_automacao_viewer").id])],
        })
        auto = self.env["mf.automation"].create({
            "name": "Fluxo HTTP Webhook TDD",
            "model_name": "crm.lead",
            "trigger_type": "webhook_received",
        })
        auto.save_builder_graph({
            "nodes": [
                {"node_key": "t1", "name": "Entrada", "category": "trigger", "node_type": "trigger_entry", "config": {}},
                {"node_key": "n1", "name": "Nota", "category": "crm_action", "node_type": "log_note", "config": {"body": "Webhook {{payload.origem}}"}},
            ],
            "edges": [{"source_key": "t1", "target_key": "n1", "branch": "default"}],
        })
        with self.assertRaises(UserError):
            auto.with_user(viewer).action_publish()
        with self.assertRaises(AccessError):
            auto.with_user(viewer).write({"name": "Hack"})

        auto.action_publish()
        lead = self.env["crm.lead"].create({"name": "Lead Webhook HTTP", "partner_id": self.partner.id, "type": "opportunity"})
        # Commit para o request HTTP de HttpCase enxergar o registro? Em Odoo 19 HttpCase compartilha a mesma transação se autenticado/single-thread, ou fazemos chamada direta se cursor isolado.
        # Em HttpCase o test_cursor roda no mesmo DB:
        url = f"/maisfibra/automacao/webhook/{auto.webhook_token}"
        resp_bad = self.url_open("/maisfibra/automacao/webhook/token-invalido", data=json.dumps({"record_id": lead.id}), headers={"Content-Type": "application/json"})
        self.assertEqual(resp_bad.status_code, 401)

        body = json.dumps({"record_id": lead.id, "event_id": "EVT-HTTP-01", "origem": "ERP"})
        resp1 = self.url_open(url, data=body, headers={"Content-Type": "application/json"})
        self.assertEqual(resp1.status_code, 200)
        d1 = json.loads(resp1.text)
        self.assertTrue(d1["ok"])
        self.assertEqual(d1["state"], "done")

        # Mesmo event_id não duplica
        resp2 = self.url_open(url, data=body, headers={"Content-Type": "application/json"})
        self.assertEqual(resp2.status_code, 200)
        d2 = json.loads(resp2.text)
        self.assertEqual(d2["state"], "skipped")
