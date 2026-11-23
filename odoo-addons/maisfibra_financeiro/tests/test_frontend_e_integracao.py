from datetime import date

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestFrontendEIntegracao(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mf_audit_off=True))
        cls.partner = cls.env["res.partner"].create({
            "name": "Cliente Frontend TDD",
            "vat": "52998224725",
            "email": "front.tdd@maisfibra.local",
            "phone": "11977776666",
        })
        if not cls.env.company.partner_id.vat:
            cls.env.company.partner_id.vat = "11222333000181"
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param("maisfibra_financeiro.arquitetura_cobranca", "a_odoo")
        ICP.set_param("maisfibra_financeiro.fiscal_ativo", "False")
        ICP.set_param("maisfibra_financeiro.sistema_mestre_fiscal", "odoo")
        ICP.set_param("maisfibra_financeiro.fiscal_ambiente", "homologacao")

    def test_01_wizard_geracao_lote(self):
        c1 = self.env["maisfibra.contrato.recorrente"].create({
            "partner_id": self.partner.id, "plano_nome": "Plano Lote A",
            "valor_mensal": 99.90, "dia_vencimento": 10, "data_inicio": "2026-01-01", "state": "ativo",
        })
        c2 = self.env["maisfibra.contrato.recorrente"].create({
            "partner_id": self.partner.id, "plano_nome": "Plano Lote B",
            "valor_mensal": 149.90, "dia_vencimento": 15, "data_inicio": "2026-01-01", "state": "ativo",
        })
        wiz = self.env["maisfibra.cobranca.lote.wizard"].create({
            "competencia": "2026-05",
            "contrato_ids": [(6, 0, [c1.id, c2.id])],
            "emitir_boletos": True,
            "enviar_clientes": False,
        })
        wiz.action_executar()
        geradas = self.env["maisfibra.cobranca"].search([
            ("contrato_id", "in", [c1.id, c2.id]), ("competencia", "=", "2026-05"),
        ])
        self.assertEqual(len(geradas), 2)
        self.assertTrue(all(c.state == "registrada" for c in geradas))

        # Reexecucao na mesma competencia nao gera duplicadas
        wiz2 = self.env["maisfibra.cobranca.lote.wizard"].create({
            "competencia": "2026-05",
            "contrato_ids": [(6, 0, [c1.id, c2.id])],
            "emitir_boletos": False,
        })
        wiz2.action_executar()
        self.assertEqual(
            self.env["maisfibra.cobranca"].search_count([("contrato_id", "in", [c1.id, c2.id]), ("competencia", "=", "2026-05")]),
            2,
        )

    def test_02_wizard_pagamento_e_cancelamento(self):
        cob = self.env["maisfibra.cobranca"].create({
            "partner_id": self.partner.id, "competencia": "2026-06",
            "data_vencimento": "2026-06-10", "valor_original": 100.0,
        })
        cob.action_emitir_boleto()

        wiz_pg = self.env["maisfibra.cobranca.pagamento.wizard"].create({
            "cobranca_id": cob.id,
            "data_pagamento": "2026-06-10",
            "valor_pago": 100.0,
        })
        wiz_pg.action_confirmar()
        self.assertEqual(cob.state, "paga")

        # Wizard cancelamento boleto
        cob2 = self.env["maisfibra.cobranca"].create({
            "partner_id": self.partner.id, "competencia": "2026-06",
            "data_vencimento": "2026-06-20", "valor_original": 100.0,
            "chave_idempotencia": f"{self.partner.id}|0|2026-06|extra1",
        })
        cob2.action_emitir_boleto()
        wiz_canc = self.env["maisfibra.cobranca.cancelar.wizard"].create({
            "alvo": "cobranca", "cobranca_id": cob2.id, "motivo": "Cancelamento via wizard de teste",
        })
        wiz_canc.action_confirmar()
        self.assertEqual(cob2.state, "cancelada")

        # Wizard cancelamento e substituicao de NF
        nf = self.env["maisfibra.nota.fiscal"].create({
            "partner_id": self.partner.id, "tipo_documento": "nfse",
            "origem_emissao": "odoo", "ambiente": "homologacao", "valor_total": 100.0,
        })
        nf.action_emitir()
        wiz_sub = self.env["maisfibra.cobranca.cancelar.wizard"].create({
            "alvo": "nf_substituir", "nota_fiscal_id": nf.id,
            "motivo": "Substituição via wizard para corrigir valor", "novo_valor": 115.0,
        })
        wiz_sub.action_confirmar()
        self.assertEqual(nf.state, "substituida")

        nf_nova = self.env["maisfibra.nota.fiscal"].search([("nf_substituida_id", "=", nf.id)], limit=1)
        self.assertTrue(nf_nova)
        wiz_canc_nf = self.env["maisfibra.cobranca.cancelar.wizard"].create({
            "alvo": "nf_cancelar", "nota_fiscal_id": nf_nova.id,
            "motivo": "Cancelamento definitivo em ambiente de homologação",
        })
        wiz_canc_nf.action_confirmar()
        self.assertEqual(nf_nova.state, "cancelada")

    def test_03_dashboard_data_kpis(self):
        c1 = self.env["maisfibra.cobranca"].create({
            "partner_id": self.partner.id, "competencia": "2026-04",
            "data_vencimento": "2026-04-10", "valor_original": 200.0,
        })
        c1.action_emitir_boleto()
        c1.action_registrar_pagamento(valor_pago=200.0, data_pagamento=date(2026, 4, 10))

        c2 = self.env["maisfibra.cobranca"].create({
            "partner_id": self.partner.id, "competencia": "2026-04",
            "data_vencimento": "2026-04-20", "valor_original": 100.0,
            "chave_idempotencia": f"{self.partner.id}|0|2026-04|extra2",
        })
        c2.action_emitir_boleto()

        data = self.env["maisfibra.cobranca"].get_dashboard_data(competencia="2026-04")
        self.assertEqual(data["competencia"], "2026-04")
        self.assertGreaterEqual(data["kpis"]["recebido"], 200.0)
        self.assertGreaterEqual(data["kpis"]["a_receber"], 100.0)
        self.assertIn("config", data)
        self.assertEqual(data["config"]["arquitetura"], "a_odoo")
        self.assertIn("distribuicao", data)
        self.assertIn("recentes", data)

    def test_04_partner_smart_buttons_e_contadores(self):
        ctr = self.env["maisfibra.contrato.recorrente"].create({
            "partner_id": self.partner.id, "plano_nome": "Plano Partner",
            "valor_mensal": 90.0, "dia_vencimento": 10, "state": "ativo",
        })
        cob = ctr.action_gerar_cobranca_competencia("2026-03", emitir=True)
        nf = self.env["maisfibra.nota.fiscal"].create({
            "partner_id": self.partner.id, "cobranca_id": cob.id,
            "tipo_documento": "nfcom", "origem_emissao": "odoo", "ambiente": "homologacao", "valor_total": 90.0,
        })
        nf.action_emitir()

        self.partner.invalidate_recordset()
        self.assertGreaterEqual(self.partner.mf_contrato_count, 1)
        self.assertGreaterEqual(self.partner.mf_cobranca_count, 1)
        self.assertGreaterEqual(self.partner.mf_nf_count, 1)
        self.assertEqual(self.partner.action_abrir_mf_contratos()["res_model"], "maisfibra.contrato.recorrente")
        self.assertEqual(self.partner.action_abrir_mf_cobrancas()["res_model"], "maisfibra.cobranca")
        self.assertEqual(self.partner.action_abrir_mf_notas_fiscais()["res_model"], "maisfibra.nota.fiscal")

    def test_05_integridade_aprovacao_existente(self):
        self.env["ir.config_parameter"].sudo().set_param("maisfibra_financeiro.aprovacao_ativa", "True")
        self.env["ir.config_parameter"].sudo().set_param("maisfibra_financeiro.limite_gerente", "5000")
        grp_ger = self.env.ref("maisfibra_financeiro.group_aprovador_gerente")
        user_ger = self.env["res.users"].create({
            "name": "Gerente Regressao", "login": "ger_reg_tdd", "email": "ger_reg@maisfibra.local",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id, grp_ger.id])],
        })
        fornecedor = self.env["res.partner"].create({"name": "Fornecedor Regressao", "supplier_rank": 1})
        journal = self.env["account.journal"].search([("type", "=", "purchase")], limit=1)
        exp_acc = self.env["account.account"].search([("account_type", "=", "expense")], limit=1)
        bill_vals = {
            "move_type": "in_invoice",
            "partner_id": fornecedor.id,
            "journal_id": journal.id,
            "invoice_date": date.today(),
            "invoice_line_ids": [(0, 0, {
                "name": "Link IP Dedicado", "account_id": exp_acc.id, "quantity": 1, "price_unit": 1000.0,
            })],
        }
        if "l10n_latam_document_number" in self.env["account.move"]._fields:
            bill_vals["l10n_latam_document_number"] = "00012345"
        bill = self.env["account.move"].create(bill_vals)
        with self.assertRaises(UserError):
            bill.action_post()

        bill.with_user(user_ger).action_mf_solicitar_aprovacao()
        bill.with_user(user_ger).action_mf_aprovar()
        bill.action_post()
        self.assertEqual(bill.state, "posted")

    def test_06_menu_raiz_aplicativo_lateral(self):
        menu = self.env.ref("maisfibra_financeiro.menu_mf_financeiro_app_root")
        self.assertFalse(menu.parent_id)
        self.assertEqual(menu.web_icon, "maisfibra_financeiro,static/description/icon.png")
        self.assertGreaterEqual(len(menu.child_id), 6)
        self.assertIn(menu.id, self.env["ir.ui.menu"]._visible_menu_ids())
