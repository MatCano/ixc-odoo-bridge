import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestComunicacaoEsperasEPipelines(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mf_audit_off=True))
        Stage = cls.env["crm.stage"].with_context(lang="pt_BR").sudo()
        stages = Stage.search([("is_won", "=", False)], order="sequence asc, id asc")
        cls.stage_1 = stages[0] if len(stages) >= 1 else Stage.create({"name": "Novo", "sequence": 1})
        cls.stage_2 = stages[1] if len(stages) >= 2 else Stage.create({"name": "Qualificado", "sequence": 2})
        cls.partner = cls.env["res.partner"].create({
            "name": "Cliente Esperas TDD",
            "email": "esperas@maisfibra.local",
            "phone": "5511955554444",
        })

    def test_01_comunicacao_whatsapp_e_espera_persistente_com_timeout(self):
        auto = self.env["mf.automation"].create({
            "name": "Followup WhatsApp + Espera",
            "model_name": "crm.lead",
            "trigger_type": "webhook_received",
        })
        auto.save_builder_graph({
            "nodes": [
                {"node_key": "t1", "name": "Webhook", "category": "trigger", "node_type": "trigger_entry", "config": {}},
                {
                    "node_key": "wa1",
                    "name": "Enviar WhatsApp",
                    "category": "communication",
                    "node_type": "send_whatsapp",
                    "config": {"body": "Olá {{contact_name}}, oferta de R$ {{expected_revenue}}!"},
                },
                {
                    "node_key": "w1",
                    "name": "Aguardar Resposta",
                    "category": "wait",
                    "node_type": "wait_reply",
                    "config": {"timeout_minutes": 30},
                },
                {
                    "node_key": "m1",
                    "name": "Mover Etapa Timeout",
                    "category": "crm_action",
                    "node_type": "move_stage",
                    "config": {"target_stage_id": self.stage_2.id},
                },
            ],
            "edges": [
                {"source_key": "t1", "target_key": "wa1", "branch": "default"},
                {"source_key": "wa1", "target_key": "w1", "branch": "default"},
                {"source_key": "w1", "target_key": "m1", "branch": "timeout"},
            ],
        })
        auto.action_publish()
        lead = self.env["crm.lead"].create({
            "name": "Lead Espera TDD",
            "partner_id": self.partner.id,
            "phone": "5511955554444",
            "expected_revenue": 199.90,
            "stage_id": self.stage_1.id,
            "type": "opportunity",
        })

        provider_cls = type(self.env["maisfibra.whatsapp.provider"])
        with patch.object(provider_cls, "send_text", return_value="WA_AUTO_01") as mock_send:
            ex = self.env["mf.automation.execution"].start_execution(auto, lead, event_key="esp:1")

        mock_send.assert_called_once()
        sent_msg = mock_send.call_args[0][1]
        self.assertIn("Cliente Esperas TDD", sent_msg)
        self.assertIn("199.90", sent_msg)
        self.assertEqual(ex.state, "waiting")
        self.assertEqual(ex.wait_type, "reply")

        # Simula timeout expirado no banco e executa cron de retomada (como após reinício)
        ex.sudo().write({"timeout_at": fields.Datetime.now() - timedelta(minutes=1)})
        self.env["mf.automation.execution"]._cron_process_queue()

        ex.invalidate_recordset()
        lead.invalidate_recordset()
        self.assertEqual(ex.state, "done")
        self.assertEqual(lead.stage_id, self.stage_2)

    def test_02_anti_loop_pipeline_e_encadeamento(self):
        auto2 = self.env["mf.automation"].create({
            "name": "Automação Filha TDD",
            "model_name": "crm.lead",
            "trigger_type": "webhook_received",
        })
        auto2.save_builder_graph({
            "nodes": [
                {"node_key": "t1", "name": "Entrada", "category": "trigger", "node_type": "trigger_entry", "config": {}},
                {"node_key": "n1", "name": "Nota Filha", "category": "crm_action", "node_type": "log_note", "config": {"body": "Filha executada"}},
            ],
            "edges": [{"source_key": "t1", "target_key": "n1", "branch": "default"}],
        })
        auto2.action_publish()

        auto1 = self.env["mf.automation"].create({
            "name": "Automação Pai Stage",
            "model_name": "crm.lead",
            "trigger_type": "stage_entered",
            "trigger_stage_id": self.stage_2.id,
        })
        auto1.save_builder_graph({
            "nodes": [
                {"node_key": "t1", "name": "Entrou Estágio", "category": "trigger", "node_type": "trigger_entry", "config": {}},
                {"node_key": "m1", "name": "Reescreve Estágio", "category": "crm_action", "node_type": "move_stage", "config": {"target_stage_id": self.stage_2.id}},
                {"node_key": "s1", "name": "Chamar Filha", "category": "flow_control", "node_type": "start_automation", "config": {"target_automation_id": auto2.id}},
            ],
            "edges": [
                {"source_key": "t1", "target_key": "m1", "branch": "default"},
                {"source_key": "m1", "target_key": "s1", "branch": "default"},
            ],
        })
        auto1.action_publish()

        lead = self.env["crm.lead"].create({
            "name": "Lead Pipeline Chain TDD",
            "partner_id": self.partner.id,
            "stage_id": self.stage_1.id,
            "type": "opportunity",
        })
        lead.write({"stage_id": self.stage_2.id})

        self.assertEqual(self.env["mf.automation.execution"].search_count([("automation_id", "=", auto1.id), ("res_id", "=", lead.id)]), 1)
        self.assertEqual(self.env["mf.automation.execution"].search_count([("automation_id", "=", auto2.id), ("res_id", "=", lead.id)]), 1)
        self.assertTrue(any("Filha executada" in (b or "") for b in lead.message_ids.mapped("body")))
