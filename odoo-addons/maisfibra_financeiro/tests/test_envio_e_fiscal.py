import base64
from datetime import date
from unittest.mock import patch

from lxml import etree

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestEnvioEFiscal(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mf_audit_off=True))
        cls.partner = cls.env["res.partner"].create({
            "name": "Cliente Envio e Fiscal",
            "vat": "52998224725",
            "email": "fiscal.cliente@maisfibra.local",
            "phone": "11988887777",
            "mf_ixc_cliente_id": "2020",
        })
        if not cls.env.company.partner_id.vat:
            cls.env.company.partner_id.vat = "11222333000181"

        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param("maisfibra_financeiro.arquitetura_cobranca", "a_odoo")
        ICP.set_param("maisfibra_financeiro.canal_envio_padrao", "email")
        ICP.set_param("maisfibra_financeiro.fiscal_ativo", "True")
        ICP.set_param("maisfibra_financeiro.fiscal_ambiente", "homologacao")
        ICP.set_param("maisfibra_financeiro.sistema_mestre_fiscal", "odoo")
        ICP.set_param("maisfibra_financeiro.fiscal_fato_gerador", "pagamento")
        ICP.set_param("maisfibra_crm.bridge_url", "http://bridge:8000")
        ICP.set_param("maisfibra_crm.bridge_token", "segredo-bridge-token-987654")

        grp_basic = cls.env.ref("base.group_user")
        grp_op = cls.env.ref("maisfibra_financeiro.group_operador_cobranca")
        grp_gestor = cls.env.ref("maisfibra_financeiro.group_gestor_financeiro")

        cls.user_sem_perm = cls.env["res.users"].create({
            "name": "Sem Perm Fin", "login": "sem_perm_fin_tdd",
            "email": "sem@maisfibra.local", "group_ids": [(6, 0, [grp_basic.id])],
        })
        cls.user_operador = cls.env["res.users"].create({
            "name": "Op Fin Env", "login": "op_fin_env_tdd",
            "email": "op_env@maisfibra.local", "group_ids": [(6, 0, [grp_basic.id, grp_op.id])],
        })
        cls.user_gestor = cls.env["res.users"].create({
            "name": "Gestor Fin Env", "login": "gestor_fin_env_tdd",
            "email": "gestor_env@maisfibra.local", "group_ids": [(6, 0, [grp_basic.id, grp_gestor.id])],
        })

    def test_01_envio_automatico_email_com_anexo_pdf_e_auditoria(self):
        cob = self.env["maisfibra.cobranca"].create({
            "partner_id": self.partner.id,
            "competencia": "2026-11",
            "data_vencimento": "2026-11-28",
            "valor_original": 149.90,
        })
        cob.action_emitir_boleto()
        cob.action_enviar_cliente(canal="email", tipo_disparo="automatico")

        self.assertEqual(cob.state, "enviada")
        self.assertTrue(cob.enviada_cliente)

        envio = self.env["maisfibra.cobranca.envio"].search([("cobranca_id", "=", cob.id)])
        self.assertEqual(len(envio), 1)
        self.assertEqual(envio.canal, "email")
        self.assertEqual(envio.tipo_disparo, "automatico")
        self.assertEqual(envio.status, "sucesso")

        mail = self.env["mail.mail"].sudo().browse(int(envio.external_id))
        self.assertTrue(mail.exists())
        self.assertTrue(mail.attachment_ids, "E-mail do boleto deve conter anexo PDF")

        # Segunda chamada automática NÃO deve duplicar envio
        cob.action_enviar_cliente(canal="email", tipo_disparo="automatico")
        self.assertEqual(self.env["maisfibra.cobranca.envio"].search_count([("cobranca_id", "=", cob.id)]), 1)

    def test_02_envio_whatsapp_e_reenvio_manual_com_permissao_e_lgpd(self):
        contrato = self.env["maisfibra.contrato.recorrente"].create({
            "partner_id": self.partner.id,
            "plano_nome": "Fibra WhatsApp",
            "valor_mensal": 99.90,
            "dia_vencimento": 10,
            "canal_envio": "whatsapp",
            "consentimento_envio": True,
            "state": "ativo",
        })
        cob = contrato.action_gerar_cobranca_competencia("2026-10", emitir=True)

        WhatsCls = type(self.env["maisfibra.whatsapp.provider"])
        with patch.object(WhatsCls, "send_media", return_value="wpp_msg_001") as m_send:
            cob.action_enviar_cliente(tipo_disparo="automatico")
            self.assertEqual(m_send.call_count, 1)

        envio_auto = self.env["maisfibra.cobranca.envio"].search([("cobranca_id", "=", cob.id)])
        self.assertEqual(len(envio_auto), 1)
        self.assertEqual(envio_auto.canal, "whatsapp")
        self.assertEqual(envio_auto.external_id, "wpp_msg_001")

        # Reenvio manual sem permissão deve ser bloqueado
        with self.assertRaises(AccessError):
            cob.with_user(self.user_sem_perm).action_reenviar_manual()

        # Reenvio manual por operador autorizado funciona e registra novo envio
        with patch.object(WhatsCls, "send_media", return_value="wpp_msg_002"):
            cob.with_user(self.user_operador).action_reenviar_manual()
        self.assertEqual(self.env["maisfibra.cobranca.envio"].search_count([("cobranca_id", "=", cob.id)]), 2)

        # Sem consentimento LGPD no contrato -> envio automático bloqueado e logado como 'ignorado'
        contrato.consentimento_envio = False
        cob_lgpd = contrato.action_gerar_cobranca_competencia("2026-12", emitir=True)
        with patch.object(WhatsCls, "send_media") as m_send2:
            cob_lgpd.action_enviar_cliente(tipo_disparo="automatico")
            self.assertEqual(m_send2.call_count, 0)
        envio_ign = self.env["maisfibra.cobranca.envio"].search([("cobranca_id", "=", cob_lgpd.id)], limit=1)
        self.assertEqual(envio_ign.status, "ignorado")

    def test_03_nota_fiscal_emissao_homologacao_xml_e_pdf(self):
        cob = self.env["maisfibra.cobranca"].create({
            "partner_id": self.partner.id,
            "competencia": "2026-11",
            "data_vencimento": "2026-11-28",
            "valor_original": 129.90,
        })
        for tipo in ("nfcom", "nfse"):
            nf = self.env["maisfibra.nota.fiscal"].create({
                "partner_id": self.partner.id,
                "cobranca_id": cob.id,
                "tipo_documento": tipo,
                "origem_emissao": "odoo",
                "ambiente": "homologacao",
                "valor_total": 129.90,
                "aliquota_iss": 2.0 if tipo == "nfse" else 0.0,
                "aliquota_icms": 18.0 if tipo == "nfcom" else 0.0,
            })
            nf.action_emitir()
            self.assertEqual(nf.state, "autorizada")
            self.assertTrue(nf.numero)
            self.assertTrue(nf.protocolo_autorizacao)
            self.assertEqual(len(nf.chave_acesso or ""), 44)
            xml_bytes = base64.b64decode(nf.xml_arquivo)
            etree.fromstring(xml_bytes)  # Deve ser XML bem-formado
            pdf_bytes = base64.b64decode(nf.pdf_arquivo)
            self.assertTrue(pdf_bytes.startswith(b"%PDF-"))

    def test_04_nota_fiscal_rejeicao_nunca_marca_autorizada(self):
        parceiro_invalido = self.env["res.partner"].create({
            "name": "Sem CPF Valido",
            "vat": "11111111111",
        })
        nf = self.env["maisfibra.nota.fiscal"].create({
            "partner_id": parceiro_invalido.id,
            "tipo_documento": "nfcom",
            "origem_emissao": "odoo",
            "ambiente": "homologacao",
            "valor_total": 100.0,
        })
        nf.action_emitir()
        self.assertEqual(nf.state, "rejeitada")
        self.assertFalse(nf.protocolo_autorizacao)
        self.assertTrue(nf.motivo_rejeicao)

    def test_05_nota_fiscal_indisponibilidade_fila_e_reprocessamento(self):
        nf = self.env["maisfibra.nota.fiscal"].create({
            "partner_id": self.partner.id,
            "tipo_documento": "nfcom",
            "origem_emissao": "ixc",
            "ambiente": "homologacao",
            "valor_total": 119.90,
        })
        BridgeCls = type(self.env["maisfibra.bridge"])
        with patch.object(BridgeCls, "call", return_value={"status": "indisponivel", "motivo_falha": "SEFAZ timeout"}):
            nf.action_emitir()

        self.assertEqual(nf.state, "indisponivel")
        self.assertFalse(nf.protocolo_autorizacao)
        exc = self.env["maisfibra.fila.excecao"].search([("nota_fiscal_id", "=", nf.id), ("state", "=", "pendente")])
        self.assertEqual(len(exc), 1)

        with patch.object(BridgeCls, "call", return_value={
            "status": "autorizada",
            "identificador_externo": "IXC-NF-777",
            "numero": "000777",
            "serie": "1",
            "chave_acesso": "35261111222333000181620010000007771000007770",
            "protocolo": "135260000777",
        }):
            exc.with_user(self.user_gestor).action_reprocessar()

        self.assertEqual(nf.state, "autorizada")
        self.assertEqual(nf.protocolo_autorizacao, "135260000777")
        self.assertEqual(exc.state, "resolvida")

    def test_06_nota_fiscal_cancelamento_e_substituicao(self):
        nf = self.env["maisfibra.nota.fiscal"].create({
            "partner_id": self.partner.id,
            "tipo_documento": "nfse",
            "origem_emissao": "odoo",
            "ambiente": "homologacao",
            "valor_total": 99.90,
        })
        nf.action_emitir()
        self.assertEqual(nf.state, "autorizada")

        # Substituicao gera nova NF vinculada autorizada e marca a antiga como substituida
        nf_sub = nf.with_user(self.user_gestor).action_substituir(
            motivo="Correção de valor e discriminação do serviço prestado",
            novo_valor=109.90,
        )
        self.assertEqual(nf.state, "substituida")
        self.assertEqual(nf_sub.state, "autorizada")
        self.assertEqual(nf_sub.nf_substituida_id.id, nf.id)
        self.assertAlmostEqual(nf_sub.valor_total, 109.90, places=2)

        # Cancelamento com motivo curto (< 15 chars) deve falhar
        with self.assertRaises(UserError):
            nf_sub.with_user(self.user_gestor).action_cancelar("Curto")

        # Cancelamento valido por gestor
        nf_sub.with_user(self.user_gestor).action_cancelar("Cancelamento em homologação por erro de cadastro")
        self.assertEqual(nf_sub.state, "cancelada")

    def test_07_fato_gerador_fiscal_por_pagamento(self):
        self.env["ir.config_parameter"].sudo().set_param("maisfibra_financeiro.fiscal_ativo", "True")
        self.env["ir.config_parameter"].sudo().set_param("maisfibra_financeiro.fiscal_fato_gerador", "pagamento")
        self.env["ir.config_parameter"].sudo().set_param("maisfibra_financeiro.sistema_mestre_fiscal", "odoo")

        cob = self.env["maisfibra.cobranca"].create({
            "partner_id": self.partner.id,
            "competencia": "2026-11",
            "data_vencimento": "2026-11-28",
            "valor_original": 135.00,
        })
        cob.action_emitir_boleto()
        self.assertFalse(cob.nota_fiscal_ids)

        cob.action_registrar_pagamento(valor_pago=135.00, data_pagamento=date.today())
        self.assertEqual(cob.state, "paga")
        self.assertEqual(len(cob.nota_fiscal_ids), 1)
        self.assertEqual(cob.nota_fiscal_ids.state, "autorizada")

    def test_08_logs_sem_dados_sensiveis(self):
        log = self.env["maisfibra.integracao.log"].registrar(
            "teste_seguranca", "sucesso",
            payload={"token": "super-secreto-xyz", "pdf_base64": "JVBERi0xLjQK" + ("Z" * 100), "valor": 50.0},
            resposta={"password": "senha-banco-123", "ok": True},
        )
        self.assertNotIn("super-secreto-xyz", log.payload_resumo or "")
        self.assertNotIn("ZZZZZZZ", log.payload_resumo or "")
        self.assertNotIn("senha-banco-123", log.resposta_resumo or "")

    def test_09_processamento_webhook_idempotente(self):
        cob = self.env["maisfibra.cobranca"].create({
            "partner_id": self.partner.id,
            "competencia": "2026-07",
            "data_vencimento": "2026-07-10",
            "valor_original": 100.0,
            "identificador_externo": "WH-BOL-9001",
        })
        cob.action_emitir_boleto()
        self.env["ir.config_parameter"].sudo().set_param("maisfibra_financeiro.fiscal_ativo", "False")

        payload = {"evento": "boleto_pago", "identificador_externo": "WH-BOL-9001", "valor_pago": 100.0, "data_pagamento": "2026-07-09"}
        res1 = self.env["maisfibra.cobranca"].processar_evento_webhook(payload)
        res2 = self.env["maisfibra.cobranca"].processar_evento_webhook(payload)

        self.assertTrue(res1["ok"])
        self.assertTrue(res2["ok"])
        self.assertEqual(cob.state, "paga")
        self.assertAlmostEqual(cob.valor_pago, 100.0, places=2)
