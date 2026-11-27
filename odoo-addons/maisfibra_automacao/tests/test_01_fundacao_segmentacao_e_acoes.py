import json
from datetime import datetime, timedelta

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestFundacaoSegmentacaoEAcoes(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mf_audit_off=True))
        cls.tag_vip = cls.env["crm.tag"].create({"name": "VIP Automação"})
        cls.partner = cls.env["res.partner"].create({
            "name": "Contato Automação TDD",
            "email": "auto@maisfibra.local",
            "phone": "5511988881111",
        })
        cls.lead = cls.env["crm.lead"].create({
            "name": "Lead Segmento VIP",
            "partner_id": cls.partner.id,
            "email_from": "auto@maisfibra.local",
            "phone": "5511988881111",
            "expected_revenue": 250.0,
            "tag_ids": [(4, cls.tag_vip.id)],
            "type": "opportunity",
        })

    def test_01_avaliador_and_or_operadores_e_bloqueio_seguranca(self):
        evaluator = self.env["mf.automation.evaluator"]
        seg = self.env["mf.automation.segment"].create({
            "name": "VIP com Receita",
            "model_name": "crm.lead",
            "rules_json": json.dumps({
                "logic": "AND",
                "rules": [
                    {"field": "tag_names", "operator": "contains", "value": "VIP"},
                    {"field": "phone", "operator": "is_set"},
                    {
                        "logic": "OR",
                        "rules": [
                            {"field": "expected_revenue", "operator": "between", "value": 200, "value_to": 300},
                            {"field": "email_from", "operator": "eq", "value": "outro@x.com"},
                        ],
                    },
                ],
            }),
        })
        self.assertTrue(seg.evaluate_record(self.lead))
        self.assertGreaterEqual(seg.member_count, 1)

        # Operador temporal in_last_days e in_segment / not_in_segment
        nested_rule = {
            "logic": "AND",
            "rules": [
                {"field": "create_date", "operator": "in_last_days", "value": 7},
                {"operator": "in_segment", "segment_id": seg.id},
            ],
        }
        self.assertTrue(evaluator.evaluate(self.lead, nested_rule))
        self.assertFalse(evaluator.evaluate(self.lead, {"operator": "not_in_segment", "segment_id": seg.id}))

        # Bloqueio de campos perigosos / dunder
        errs = evaluator.validate_rules("crm.lead", {"field": "__class__.password", "operator": "eq", "value": "x"})
        self.assertTrue(errs)
        self.assertFalse(evaluator.evaluate(self.lead, {"field": "user_id.password", "operator": "is_set"}))

    def test_02_gatilho_lead_criado_e_acoes_crm_completas(self):
        user_target = self.env["res.users"].create({
            "name": "Vendedor Automação TDD",
            "login": "vend_auto_tdd",
            "email": "vend_auto@maisfibra.local",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        auto = self.env["mf.automation"].create({
            "name": "Fluxo Boas-vindas CRM TDD",
            "model_name": "crm.lead",
            "trigger_type": "lead_created",
            "entry_filter_json": json.dumps({"logic": "AND", "rules": [{"field": "phone", "operator": "is_set"}]}),
        })
        n_trig = auto.node_ids
        n_tag = self.env["mf.automation.node"].create({
            "automation_id": auto.id,
            "node_key": "tag_1",
            "name": "Adicionar Tag Qualificado",
            "category": "crm_action",
            "node_type": "add_tag",
            "config_json": json.dumps({"tag_name": "AutoTag"}),
        })
        n_owner = self.env["mf.automation.node"].create({
            "automation_id": auto.id,
            "node_key": "owner_1",
            "name": "Atribuir Vendedor",
            "category": "crm_action",
            "node_type": "assign_owner",
            "config_json": json.dumps({"user_id": user_target.id}),
        })
        n_task = self.env["mf.automation.node"].create({
            "automation_id": auto.id,
            "node_key": "task_1",
            "name": "Criar Tarefa",
            "category": "crm_action",
            "node_type": "create_task",
            "config_json": json.dumps({"summary": "Ligar para {{contact_name}}", "due_days": 2}),
        })
        n_note = self.env["mf.automation.node"].create({
            "automation_id": auto.id,
            "node_key": "note_1",
            "name": "Registrar Nota",
            "category": "crm_action",
            "node_type": "log_note",
            "config_json": json.dumps({"body": "Lead {{lead_name}} processado."}),
        })
        Edge = self.env["mf.automation.edge"]
        Edge.create({"automation_id": auto.id, "source_node_id": n_trig.id, "target_node_id": n_tag.id, "branch": "default"})
        Edge.create({"automation_id": auto.id, "source_node_id": n_tag.id, "target_node_id": n_owner.id, "branch": "default"})
        Edge.create({"automation_id": auto.id, "source_node_id": n_owner.id, "target_node_id": n_task.id, "branch": "default"})
        Edge.create({"automation_id": auto.id, "source_node_id": n_task.id, "target_node_id": n_note.id, "branch": "default"})

        auto.action_publish()
        self.assertEqual(auto.state, "active")

        novo_lead = self.env["crm.lead"].create({
            "name": "Lead Trigger Novo TDD",
            "partner_id": self.partner.id,
            "phone": "5511977772222",
            "type": "opportunity",
        })
        self.assertIn("AutoTag", novo_lead.tag_ids.mapped("name"))
        self.assertEqual(novo_lead.user_id, user_target)
        self.assertTrue(any("processado" in (b or "") for b in novo_lead.message_ids.mapped("body")))

        exec_rec = self.env["mf.automation.execution"].search([
            ("automation_id", "=", auto.id),
            ("res_id", "=", novo_lead.id),
        ], limit=1)
        self.assertEqual(exec_rec.state, "done")
        self.assertGreaterEqual(len(exec_rec.step_log_ids), 5)

    def test_03_marcar_ganho_exige_autorizacao_explicita(self):
        auto = self.env["mf.automation"].create({"name": "Teste Autorização Ganho", "model_name": "crm.lead"})
        n_won = self.env["mf.automation.node"].create({
            "automation_id": auto.id,
            "node_key": "won_1",
            "name": "Ganhar Sem Permissão",
            "category": "crm_action",
            "node_type": "mark_won",
            "config_json": json.dumps({"authorized": False}),
        })
        self.env["mf.automation.edge"].create({
            "automation_id": auto.id,
            "source_node_id": auto.node_ids[0].id,
            "target_node_id": n_won.id,
            "branch": "default",
        })
        res = auto.action_validate_flow()
        self.assertFalse(res["valid"])
        self.assertIn("won_1", res["node_errors"])
