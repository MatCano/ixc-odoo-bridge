import json

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestConstrutorValidacaoEVersionamento(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mf_audit_off=True))
        cls.partner = cls.env["res.partner"].create({"name": "Cliente Versão TDD", "phone": "5511912345678"})

    def test_01_validacao_fluxo_desconectado_condicao_incompleta_e_ciclo(self):
        auto = self.env["mf.automation"].create({"name": "Fluxo Inválido TDD", "model_name": "crm.lead"})
        n_trig = auto.node_ids[0]
        n_cond = self.env["mf.automation.node"].create({
            "automation_id": auto.id,
            "node_key": "cond_1",
            "name": "Condição Sem Falso",
            "category": "condition",
            "node_type": "condition_if",
            "config_json": json.dumps({"rules": {"logic": "AND", "rules": [{"field": "phone", "operator": "is_set"}]}}),
        })
        n_loose = self.env["mf.automation.node"].create({
            "automation_id": auto.id,
            "node_key": "loose_1",
            "name": "Bloco Solto",
            "category": "crm_action",
            "node_type": "log_note",
            "config_json": json.dumps({"body": "Oi"}),
        })
        self.env["mf.automation.edge"].create({
            "automation_id": auto.id,
            "source_node_id": n_trig.id,
            "target_node_id": n_cond.id,
            "branch": "default",
        })
        self.env["mf.automation.edge"].create({
            "automation_id": auto.id,
            "source_node_id": n_cond.id,
            "target_node_id": n_trig.id,  # ciclo sem espera!
            "branch": "true",
        })

        val = auto.action_validate_flow()
        self.assertFalse(val["valid"])
        self.assertIn("loose_1", val["node_errors"])
        self.assertIn("cond_1", val["node_errors"])
        self.assertTrue(any("Ciclo fechado" in err for err in val["errors"]))

        with self.assertRaises(UserError):
            auto.action_publish()

    def test_02_construtor_save_preview_e_versionamento_imutavel(self):
        auto = self.env["mf.automation"].create({
            "name": "Fluxo Versionado TDD",
            "model_name": "crm.lead",
            "trigger_type": "webhook_received",
        })
        payload = {
            "automation": {"name": "Fluxo Versionado TDD"},
            "nodes": [
                {"node_key": "trigger_1", "name": "Webhook", "category": "trigger", "node_type": "trigger_entry", "pos_x": 50, "pos_y": 50, "config": {}},
                {"node_key": "wait_1", "name": "Esperar Resposta", "category": "wait", "node_type": "wait_reply", "pos_x": 50, "pos_y": 200, "config": {"timeout_minutes": 60}},
                {"node_key": "note_v1", "name": "Nota V1", "category": "crm_action", "node_type": "log_note", "pos_x": 50, "pos_y": 350, "config": {"body": "Executou V1"}},
            ],
            "edges": [
                {"source_key": "trigger_1", "target_key": "wait_1", "branch": "default"},
                {"source_key": "wait_1", "target_key": "note_v1", "branch": "reply"},
            ],
        }
        builder_data = auto.save_builder_graph(payload)
        self.assertTrue(builder_data["validation"]["valid"])
        self.assertTrue(builder_data["preview_paths"])

        auto.action_publish()
        v1 = auto.published_version_id
        self.assertEqual(v1.version_number, 1)

        lead = self.env["crm.lead"].create({"name": "Lead Em Execução V1", "partner_id": self.partner.id, "type": "opportunity"})
        exec_v1 = self.env["mf.automation.execution"].start_execution(auto, lead, event_key="ver:1")
        self.assertEqual(exec_v1.state, "waiting")
        self.assertEqual(exec_v1.version_id, v1)

        # Publica V2 alterando a nota para "Executou V2"
        payload["nodes"][2]["config"] = {"body": "Executou V2"}
        auto.save_builder_graph(payload)
        auto.action_publish()
        v2 = auto.published_version_id
        self.assertEqual(v2.version_number, 2)

        # Execução iniciada em V1 permanece presa em V1 (não muda retroativamente!)
        exec_v1.invalidate_recordset()
        self.assertEqual(exec_v1.version_id, v1)
        self.env["mf.automation.execution"].resume_waiting_on_event("crm.lead", lead.id, "reply_received")
        exec_v1.invalidate_recordset()
        self.assertEqual(exec_v1.state, "done")
        self.assertTrue(any("Executou V1" in (b or "") for b in lead.message_ids.mapped("body")))
