from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCrmLeadStageEFatura(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mf_audit_off=True))
        Stage = cls.env["crm.stage"].with_context(lang="pt_BR").sudo()
        open_stages = Stage.search([("is_won", "=", False), ("fold", "=", False)], order="sequence asc, id asc")
        cls.stage_novo = open_stages[0] if len(open_stages) >= 1 else Stage.create({"name": "Novo", "sequence": 1})
        cls.stage_qual = open_stages[1] if len(open_stages) >= 2 else Stage.create({"name": "Qualificado", "sequence": 2})
        cls.stage_prop = open_stages[2] if len(open_stages) >= 3 else Stage.create({"name": "Proposta", "sequence": 3})
        cls.stage_ganho = Stage.search([("is_won", "=", True)], order="sequence asc, id asc", limit=1) or Stage.create({"name": "Ganho", "sequence": 70, "is_won": True})

        cls.partner = cls.env["res.partner"].create({
            "name": "Cliente Lead Fatura TDD",
            "email": "lead.fatura@maisfibra.local",
            "phone": "5511988889999",
            "vat": "52998224725",
        })
        if not cls.env.company.partner_id.vat:
            cls.env.company.partner_id.vat = "11222333000181"
        cls.env["ir.config_parameter"].sudo().set_param("maisfibra_financeiro.arquitetura_cobranca", "a_odoo")

    def test_01_navegacao_etapas_next_e_back(self):
        lead = self.env["crm.lead"].create({
            "name": "Lead Navegação TDD",
            "type": "opportunity",
            "partner_id": self.partner.id,
            "stage_id": self.stage_novo.id,
        })
        self.assertFalse(lead.mf_can_stage_back)
        self.assertTrue(lead.mf_can_stage_next)
        self.assertEqual(lead.mf_next_stage_id, self.stage_qual)

        with self.assertRaises(UserError):
            lead.action_mf_stage_back()

        lead.action_mf_stage_next()
        self.assertEqual(lead.stage_id, self.stage_qual)
        self.assertTrue(lead.mf_can_stage_back)
        self.assertEqual(lead.mf_next_stage_id, self.stage_prop)

        lead.action_mf_stage_next()
        self.assertEqual(lead.stage_id, self.stage_prop)
        self.assertTrue(lead.mf_next_stage_is_won)

        lead.action_mf_stage_back()
        self.assertEqual(lead.stage_id, self.stage_qual)

        lead.action_mf_stage_back()
        self.assertEqual(lead.stage_id, self.stage_novo)

    def test_02_avancar_para_ganho_emite_fatura_automaticamente(self):
        lead = self.env["crm.lead"].create({
            "name": "Lead Ganho Fatura TDD",
            "type": "opportunity",
            "partner_id": self.partner.id,
            "stage_id": self.stage_prop.id,
            "expected_revenue": 149.90,
        })
        self.assertEqual(lead.mf_invoice_count, 0)
        self.assertTrue(lead.mf_next_stage_is_won)

        lead.action_mf_stage_next()
        self.assertEqual(lead.stage_id, self.stage_ganho)
        self.assertEqual(lead.won_status, "won")
        self.assertEqual(lead.mf_invoice_count, 1)

        fatura = lead.mf_invoice_ids[:1]
        self.assertEqual(fatura.move_type, "out_invoice")
        self.assertEqual(fatura.state, "posted")
        self.assertAlmostEqual(fatura.amount_total, 149.90, places=2)
        self.assertEqual(fatura.partner_id, self.partner)
        self.assertTrue(lead.mf_cobranca_id)
        self.assertEqual(lead.mf_cobranca_id.move_id, fatura)

    def test_03_idempotencia_back_e_next_nao_duplica_fatura(self):
        lead = self.env["crm.lead"].create({
            "name": "Lead Idempotencia Fatura TDD",
            "type": "opportunity",
            "partner_id": self.partner.id,
            "stage_id": self.stage_prop.id,
            "expected_revenue": 199.90,
        })
        lead.action_mf_stage_next()
        self.assertEqual(lead.mf_invoice_count, 1)
        fatura_id = lead.mf_invoice_ids.id

        lead.action_mf_stage_back()
        self.assertEqual(lead.stage_id, self.stage_prop)
        self.assertEqual(lead.won_status, "pending")

        lead.action_mf_stage_next()
        self.assertEqual(lead.stage_id, self.stage_ganho)
        self.assertEqual(lead.mf_invoice_count, 1)
        self.assertEqual(lead.mf_invoice_ids.id, fatura_id)

    def test_04_lead_sem_parceiro_e_receita_zero_emite_fatura_sem_erro(self):
        lead = self.env["crm.lead"].create({
            "name": "[WhatsApp] Cliente Novo Sem Parceiro TDD",
            "type": "opportunity",
            "contact_name": "Cliente Novo Sem Parceiro TDD",
            "phone": "5511977771234",
            "stage_id": self.stage_prop.id,
            "expected_revenue": 0.0,
        })
        lead.action_mf_stage_next()
        self.assertEqual(lead.stage_id, self.stage_ganho)
        self.assertTrue(lead.partner_id)
        self.assertEqual(lead.mf_invoice_count, 1)
        fatura = lead.mf_invoice_ids[:1]
        self.assertEqual(fatura.state, "posted")
        self.assertAlmostEqual(fatura.amount_total, 0.0, places=2)
        self.assertFalse(lead.mf_cobranca_id)

    def test_05_action_mf_view_invoices_retorna_acao_correta(self):
        lead = self.env["crm.lead"].create({
            "name": "Lead Action View Fatura TDD",
            "type": "opportunity",
            "partner_id": self.partner.id,
            "stage_id": self.stage_prop.id,
            "expected_revenue": 99.90,
        })
        lead.action_mf_stage_next()
        act = lead.action_mf_view_invoices()
        self.assertEqual(act["res_model"], "account.move")
        self.assertEqual(act["view_mode"], "form")
        self.assertEqual(act["res_id"], lead.mf_invoice_ids.id)
