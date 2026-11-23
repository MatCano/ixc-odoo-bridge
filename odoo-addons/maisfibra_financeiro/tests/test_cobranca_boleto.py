import base64
from datetime import date, timedelta
from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCobrancaBoleto(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mf_audit_off=True))
        cls.partner = cls.env["res.partner"].create({
            "name": "Cliente Fibra Teste",
            "vat": "52998224725",  # CPF valido algoritmicamente
            "email": "cliente.teste@maisfibra.local",
            "phone": "11999990000",
            "street": "Rua das Flores, 100",
            "city": "São Paulo",
            "zip": "01001-000",
        })
        cls.company_partner = cls.env.company.partner_id
        if not cls.company_partner.vat:
            cls.company_partner.vat = "11222333000181"  # CNPJ valido

        # Configurar parametros padrao de cobranca
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param("maisfibra_financeiro.arquitetura_cobranca", "a_odoo")
        ICP.set_param("maisfibra_financeiro.banco_codigo", "756")
        ICP.set_param("maisfibra_financeiro.banco_agencia", "0001")
        ICP.set_param("maisfibra_financeiro.banco_conta", "0012345")
        ICP.set_param("maisfibra_financeiro.banco_carteira", "109")
        ICP.set_param("maisfibra_financeiro.multa_percentual", "2.0")
        ICP.set_param("maisfibra_financeiro.juros_mensal_percentual", "1.0")
        ICP.set_param("maisfibra_crm.bridge_url", "http://bridge:8000")
        ICP.set_param("maisfibra_crm.bridge_token", "token-teste-seguro")

        # Usuarios com perfis diferentes
        grp_operador = cls.env.ref("maisfibra_financeiro.group_operador_cobranca")
        grp_gestor = cls.env.ref("maisfibra_financeiro.group_gestor_financeiro")
        grp_basic = cls.env.ref("base.group_user")

        cls.user_operador = cls.env["res.users"].create({
            "name": "Operador Cobranca",
            "login": "op_cobranca_tdd",
            "email": "op@maisfibra.local",
            "group_ids": [(6, 0, [grp_basic.id, grp_operador.id])],
        })
        cls.user_gestor = cls.env["res.users"].create({
            "name": "Gestor Financeiro",
            "login": "gestor_fin_tdd",
            "email": "gestor@maisfibra.local",
            "group_ids": [(6, 0, [grp_basic.id, grp_gestor.id])],
        })

    def test_01_contrato_e_geracao_cobranca_idempotente(self):
        contrato = self.env["maisfibra.contrato.recorrente"].create({
            "partner_id": self.partner.id,
            "plano_nome": "Fibra 600 Mega",
            "valor_mensal": 120.0,
            "dia_vencimento": 10,
            "data_inicio": "2026-01-01",
            "prorata_ativa": False,
            "state": "ativo",
        })

        cob1 = contrato.action_gerar_cobranca_competencia("2026-11")
        cob2 = contrato.action_gerar_cobranca_competencia("2026-11")

        self.assertEqual(cob1.id, cob2.id, "Deve retornar a mesma cobrança sem duplicar")
        self.assertEqual(cob1.valor_original, 120.0)
        self.assertEqual(str(cob1.data_vencimento), "2026-11-10")
        self.assertEqual(cob1.state, "criada")

        total = self.env["maisfibra.cobranca"].search_count([
            ("partner_id", "=", self.partner.id),
            ("contrato_id", "=", contrato.id),
            ("competencia", "=", "2026-11"),
        ])
        self.assertEqual(total, 1)

        # Constraint única em chave_idempotencia no banco
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.env["maisfibra.cobranca"].create({
                "partner_id": self.partner.id,
                "contrato_id": contrato.id,
                "competencia": "2026-11",
                "data_vencimento": "2026-11-10",
                "valor_original": 120.0,
                "chave_idempotencia": cob1.chave_idempotencia,
            })

    def test_02_calculo_prorata_e_desconto(self):
        contrato = self.env["maisfibra.contrato.recorrente"].create({
            "partner_id": self.partner.id,
            "plano_nome": "Fibra 1 Giga",
            "valor_mensal": 120.0,
            "dia_vencimento": 25,
            "data_inicio": "2026-11-16",  # 15 dias restantes de 30 (16..30) -> R$ 60.00
            "prorata_ativa": True,
            "desconto_pontualidade": 10.0,
            "state": "ativo",
        })
        cob = contrato.action_gerar_cobranca_competencia("2026-11")
        self.assertAlmostEqual(cob.valor_original, 60.0, places=2)
        self.assertAlmostEqual(cob.valor_desconto, 10.0, places=2)
        self.assertAlmostEqual(cob.valor_liquido, 50.0, places=2)

    def test_03_emissao_boleto_alternativa_a_pdf_e_febraban(self):
        from ..models.boleto_pdf_engine import validar_linha_digitavel

        contrato = self.env["maisfibra.contrato.recorrente"].create({
            "partner_id": self.partner.id,
            "plano_nome": "Fibra 500 Mega",
            "valor_mensal": 99.90,
            "dia_vencimento": 15,
            "data_inicio": "2026-01-01",
            "state": "ativo",
        })
        cob = contrato.action_gerar_cobranca_competencia("2026-11")
        cob.with_user(self.user_operador).action_emitir_boleto()

        self.assertEqual(cob.state, "registrada")
        self.assertTrue(cob.nosso_numero)
        self.assertEqual(len(cob.codigo_barras or ""), 44)
        self.assertTrue((cob.codigo_barras or "").isdigit())
        self.assertTrue(validar_linha_digitavel(cob.linha_digitavel))
        self.assertTrue(cob.pix_copia_cola)
        self.assertTrue(cob.access_token)
        self.assertIn(cob.access_token, cob.link_pagamento or "")

        pdf_bytes = base64.b64decode(cob.boleto_pdf)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertGreater(len(pdf_bytes), 1024)

        log = self.env["maisfibra.integracao.log"].search([
            ("cobranca_id", "=", cob.id),
            ("operacao", "=", "emitir_boleto"),
            ("status", "=", "sucesso"),
        ], limit=1)
        self.assertTrue(log, "Deve registrar log de sucesso na emissao do boleto")

    def test_04_emissao_boleto_ixc_e_indisponibilidade_fila_excecao(self):
        self.env["ir.config_parameter"].sudo().set_param("maisfibra_financeiro.arquitetura_cobranca", "b_ixc")
        self.partner.mf_ixc_cliente_id = "777"
        contrato = self.env["maisfibra.contrato.recorrente"].create({
            "partner_id": self.partner.id,
            "plano_nome": "Fibra 400 Mega",
            "ixc_contrato_id": "888",
            "valor_mensal": 89.90,
            "dia_vencimento": 20,
            "data_inicio": "2026-01-01",
            "state": "ativo",
        })
        cob = contrato.action_gerar_cobranca_competencia("2026-11")

        BridgeCls = type(self.env["maisfibra.bridge"])
        with patch.object(BridgeCls, "call", side_effect=UserError("Não foi possível falar com o bridge IXC: timeout")):
            cob.action_emitir_boleto()

        self.assertEqual(cob.state, "falha")
        excecao = self.env["maisfibra.fila.excecao"].search([
            ("cobranca_id", "=", cob.id),
            ("state", "=", "pendente"),
        ])
        self.assertEqual(len(excecao), 1, "Deve enfileirar na fila de excecoes")
        self.assertEqual(excecao.tentativas, 1)

        # Restaurar bridge e reprocessar excecao sem duplicar cobranca
        fake_pdf_b64 = base64.b64encode(b"%PDF-1.4 IXC BOLETO REAL CONTENT").decode()
        mock_resp = {
            "identificador_externo": "99501",
            "status": "registrada",
            "linha_digitavel": "75690.00109 00000.009952 01000.000018 1 16380000008990",
            "codigo_barras": "75691163800000089900001090000009950100000001",
            "nosso_numero": "00000099501",
            "link_pagamento": "https://boleto.ixc.local/99501",
            "pdf_base64": fake_pdf_b64,
        }
        with patch.object(BridgeCls, "call", return_value=mock_resp):
            excecao.with_user(self.user_gestor).action_reprocessar()

        self.assertEqual(cob.state, "registrada")
        self.assertEqual(cob.identificador_externo, "99501")
        self.assertEqual(excecao.state, "resolvida")
        self.assertEqual(self.env["maisfibra.cobranca"].search_count([("id", "=", cob.id)]), 1)

    def test_05_ciclo_status_vencida_pagamento_com_juros_e_cancelamento(self):
        contrato = self.env["maisfibra.contrato.recorrente"].create({
            "partner_id": self.partner.id,
            "plano_nome": "Fibra 300 Mega",
            "valor_mensal": 100.0,
            "dia_vencimento": 10,
            "data_inicio": "2026-01-01",
            "state": "ativo",
        })
        venc_passado = date.today() - timedelta(days=10)
        cob = self.env["maisfibra.cobranca"].create({
            "partner_id": self.partner.id,
            "contrato_id": contrato.id,
            "competencia": venc_passado.strftime("%Y-%m"),
            "data_vencimento": venc_passado,
            "valor_original": 100.0,
        })
        cob.action_emitir_boleto()
        self.assertEqual(cob.state, "registrada")

        self.env["maisfibra.cobranca"]._cron_atualizar_vencidas()
        self.assertEqual(cob.state, "vencida")

        # Multa 2% = 2.00; Juros 1% ao mes * 10 dias = 0.33 -> total ~ 102.33
        encargos = cob.calcular_encargos(data_referencia=date.today())
        self.assertAlmostEqual(encargos["multa"], 2.0, places=2)
        self.assertGreater(encargos["juros"], 0.30)
        self.assertAlmostEqual(encargos["total"], 100.0 + encargos["multa"] + encargos["juros"], places=2)

        cob.action_registrar_pagamento(valor_pago=encargos["total"], data_pagamento=date.today())
        self.assertEqual(cob.state, "paga")

        # Nao pode cancelar cobranca ja paga
        with self.assertRaises(UserError):
            cob.with_user(self.user_gestor).action_cancelar("Teste cancelar paga")

        # Operador comum sem group_gestor_financeiro nao pode cancelar cobranca aberta
        cob_aberta = self.env["maisfibra.cobranca"].create({
            "partner_id": self.partner.id,
            "contrato_id": contrato.id,
            "competencia": "2027-01",
            "data_vencimento": "2027-01-10",
            "valor_original": 100.0,
        })
        cob_aberta.action_emitir_boleto()
        with self.assertRaises(AccessError):
            cob_aberta.with_user(self.user_operador).action_cancelar("Sem permissao")

        cob_aberta.with_user(self.user_gestor).action_cancelar("Solicitação do cliente")
        self.assertEqual(cob_aberta.state, "cancelada")
        self.assertEqual(cob_aberta.motivo_cancelamento, "Solicitação do cliente")

    def test_06_segunda_via_rastreavel(self):
        venc_passado = date.today() - timedelta(days=15)
        cob1 = self.env["maisfibra.cobranca"].create({
            "partner_id": self.partner.id,
            "competencia": "2026-08",
            "data_vencimento": venc_passado,
            "valor_original": 100.0,
        })
        cob1.action_emitir_boleto()
        cob1._cron_atualizar_vencidas()
        self.assertEqual(cob1.state, "vencida")

        novo_venc = date.today() + timedelta(days=5)
        cob2 = cob1.with_user(self.user_operador).action_gerar_segunda_via(nova_data_vencimento=novo_venc)

        self.assertEqual(cob1.state, "cancelada")
        self.assertEqual(cob2.cobranca_origem_id.id, cob1.id)
        self.assertEqual(cob2.numero_via, 2)
        self.assertEqual(cob2.state, "registrada")
        self.assertEqual(cob2.data_vencimento, novo_venc)
        self.assertGreater(cob2.valor_original, 100.0)
        self.assertNotEqual(cob2.chave_idempotencia, cob1.chave_idempotencia)

    def test_07_importacao_ixc_idempotente(self):
        self.partner.mf_ixc_cliente_id = "10"
        contrato = self.env["maisfibra.contrato.recorrente"].create({
            "partner_id": self.partner.id,
            "plano_nome": "Fibra IXC",
            "ixc_contrato_id": "101",
            "valor_mensal": 119.90,
            "dia_vencimento": 10,
            "data_inicio": "2026-01-01",
            "state": "ativo",
        })
        lote_1 = [{
            "identificador_externo": "IXC-FAT-5501",
            "ixc_cliente_id": "10",
            "ixc_contrato_id": "101",
            "competencia": "2026-10",
            "data_vencimento": "2026-10-10",
            "valor": 119.90,
            "status": "registrada",
            "linha_digitavel": "75690.00109 00000.005501 01000.000018 1 16380000011990",
            "nosso_numero": "005501",
        }]
        res1 = self.env["maisfibra.cobranca"].sincronizar_lote_ixc(lote_1)
        self.assertEqual(res1["criadas"], 1)

        lote_2 = [dict(lote_1[0], status="paga", valor_pago=119.90, data_pagamento="2026-10-09")]
        res2 = self.env["maisfibra.cobranca"].sincronizar_lote_ixc(lote_2)
        self.assertEqual(res2["criadas"], 0)
        self.assertEqual(res2["atualizadas"], 1)

        cob = self.env["maisfibra.cobranca"].search([("identificador_externo", "=", "IXC-FAT-5501")])
        self.assertEqual(len(cob), 1)
        self.assertEqual(cob.contrato_id.id, contrato.id)
        self.assertEqual(cob.state, "paga")
        self.assertAlmostEqual(cob.valor_pago, 119.90, places=2)
        self.assertEqual(str(cob.data_pagamento), "2026-10-09")
