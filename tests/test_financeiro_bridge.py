"""Testes TDD do Sprint 1 — Backend Bridge IXC Financeiro + Motor FEBRABAN."""
import base64
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SYNC_STATE_PATH", os.path.join(tempfile.mkdtemp(), "state_fin.json"))
os.environ.setdefault("BRIDGE_TOKEN", "teste-token-bridge")
os.environ.setdefault("WEBHOOK_SECRET", "teste-webhook-secret")

import ixc  # noqa: E402
import financeiro_ixc  # noqa: E402
import main as bridge_main  # noqa: E402
from fastapi import HTTPException  # noqa: E402


class TestFebrabanESanitizacao(unittest.TestCase):
    def test_01_fator_vencimento_pre_e_pos_reset_2025(self):
        # Antes do reset de 22/02/2025 (base 1997-10-07)
        self.assertEqual(financeiro_ixc.calcular_fator_vencimento("1997-10-07"), "0000")
        self.assertEqual(financeiro_ixc.calcular_fator_vencimento("2000-07-03"), "1000")
        self.assertEqual(financeiro_ixc.calcular_fator_vencimento("2025-02-21"), "9999")
        # Reset FEBRABAN em 22/02/2025 -> volta para 1000
        self.assertEqual(financeiro_ixc.calcular_fator_vencimento("2025-02-22"), "1000")
        self.assertEqual(financeiro_ixc.calcular_fator_vencimento("2026-11-22"), "1638")

    def test_02_codigo_barras_44_e_linha_digitavel_47_com_mod10_e_mod11(self):
        dados = financeiro_ixc.gerar_codigo_barras_e_linha_digitavel(
            banco="756",
            vencimento="2026-11-22",
            valor=149.90,
            nosso_numero="00012345",
            agencia="0001",
            conta="0012345",
            carteira="109",
        )
        barra = dados["codigo_barras"]
        linha = dados["linha_digitavel"]
        linha_digitos = "".join(c for c in linha if c.isdigit())

        self.assertEqual(len(barra), 44)
        self.assertTrue(barra.isdigit())
        self.assertEqual(barra[:3], "756")
        self.assertEqual(barra[3], "9")  # moeda BRL
        self.assertEqual(barra[5:9], "1638")
        self.assertEqual(barra[9:19], "0000014990")

        # Verifica DV geral Modulo 11 na posicao 5 (indice 4)
        sem_dv = barra[:4] + barra[5:]
        self.assertEqual(barra[4], str(financeiro_ixc.modulo11_boleto(sem_dv)))

        # Linha digitavel formatada e com 47 digitos
        self.assertEqual(len(linha_digitos), 47)
        self.assertRegex(linha, r"^\d{5}\.\d{5} \d{5}\.\d{6} \d{5}\.\d{6} \d \d{14}$")

        # Valida Mod10 dos campos 1, 2 e 3
        campo1, dv1 = linha_digitos[0:9], linha_digitos[9]
        campo2, dv2 = linha_digitos[10:20], linha_digitos[20]
        campo3, dv3 = linha_digitos[21:31], linha_digitos[31]
        self.assertEqual(dv1, str(financeiro_ixc.modulo10(campo1)))
        self.assertEqual(dv2, str(financeiro_ixc.modulo10(campo2)))
        self.assertEqual(dv3, str(financeiro_ixc.modulo10(campo3)))
        self.assertTrue(financeiro_ixc.validar_linha_digitavel(linha))

        # Alterando um digito deve invalidar
        linha_invalida = ("9" if linha[0] != "9" else "8") + linha[1:]
        self.assertFalse(financeiro_ixc.validar_linha_digitavel(linha_invalida))

    def test_03_sanitizacao_logs_nao_expoe_segredos_nem_blobs(self):
        bruto = {
            "token": "segredo-super-123",
            "password": "minha-senha-secreta",
            "senha": "abc",
            "authorization": "Bearer xyz999",
            "pdf_base64": "JVBERi0xLjQK" + ("A" * 500),
            "xml_base64": "PD94bWwgdmVyc2lvbj0iMS4wIj8+" + ("B" * 500),
            "nested": {
                "api_key": "key-privada",
                "cliente_id": "42",
                "valor": 99.90,
            },
        }
        limpo = financeiro_ixc.sanitizar_payload(bruto)
        dump = json.dumps(limpo)
        self.assertNotIn("segredo-super-123", dump)
        self.assertNotIn("minha-senha-secreta", dump)
        self.assertNotIn("Bearer xyz999", dump)
        self.assertNotIn("key-privada", dump)
        self.assertNotIn("AAAA" * 20, dump)
        self.assertEqual(limpo["nested"]["cliente_id"], "42")
        self.assertEqual(limpo["nested"]["valor"], 99.90)


class TestOperacoesFinanceiroIXC(unittest.TestCase):
    def setUp(self):
        self.db = {
            "cliente": {
                "10": {"id": "10", "razao": "Cliente Teste", "cnpj_cpf": "12345678901"}
            },
            "cliente_contrato": {
                "101": {
                    "id": "101",
                    "id_cliente": "10",
                    "contrato": "Fibra 600 Mega",
                    "status": "A",
                    "status_internet": "A",
                    "valor_contrato": "119.90",
                    "dia_vencimento": "10",
                    "data_ativacao": "2026-01-10",
                },
                "102": {
                    "id": "102",
                    "id_cliente": "10",
                    "contrato": "Contrato Antigo",
                    "status": "I",
                    "status_internet": "D",
                    "valor_contrato": "79.90",
                },
            },
            "fn_areceber": {
                "501": {
                    "id": "501",
                    "id_cliente": "10",
                    "id_contrato": "101",
                    "data_vencimento": (date.today() + timedelta(days=10)).isoformat(),
                    "valor": "119.90",
                    "valor_aberto": "119.90",
                    "status": "A",
                    "linha_digitavel": "75690.00109 00000.000501 00001.234507 1 16380000011990",
                    "codigo_barras": "75691163800000119900001090000000050100001234",
                    "nn_boleto": "00000501",
                    "obs": "Comp: 2026-10",
                },
                "502": {
                    "id": "502",
                    "id_cliente": "10",
                    "id_contrato": "101",
                    "data_vencimento": (date.today() - timedelta(days=5)).isoformat(),
                    "valor": "119.90",
                    "valor_aberto": "119.90",
                    "status": "A",
                },
                "503": {
                    "id": "503",
                    "id_cliente": "10",
                    "id_contrato": "101",
                    "data_vencimento": "2026-09-10",
                    "valor": "119.90",
                    "valor_aberto": "0.00",
                    "status": "R",
                    "data_pagamento": "2026-09-09",
                    "valor_recebido": "119.90",
                },
                "504": {
                    "id": "504",
                    "id_cliente": "10",
                    "id_contrato": "101",
                    "data_vencimento": "2026-08-10",
                    "valor": "119.90",
                    "valor_aberto": "0.00",
                    "status": "C",
                },
            },
            "vd_saida": {
                "901": {
                    "id": "901",
                    "id_cliente": "10",
                    "id_areceber": "503",
                    "numero_nf": "000901",
                    "serie": "1",
                    "chave_nf": "35260912345678000190620010000009011000009010",
                    "protocolo": "135260000123456",
                    "status": "A",
                    "modelo": "62",
                    "valor_total": "119.90",
                    "data_emissao": "2026-09-05",
                }
            },
        }
        self._seq = 1000

        def mock_listar(tabela, campo="id", valor="0", oper="=", pagina=1, rp=None, **kw):
            regs = list(self.db.get(tabela, {}).values())
            if oper == "=":
                regs = [r for r in regs if str(r.get(campo, "")) == str(valor)]
            elif oper == ">=":
                regs = [r for r in regs if str(r.get(campo, "")) >= str(valor)]
            elif oper == ">":
                try:
                    regs = [r for r in regs if int(r.get(campo, 0)) > int(valor)]
                except ValueError:
                    regs = [r for r in regs if str(r.get(campo, "")) > str(valor)]
            return regs if pagina == 1 else []

        def mock_buscar(tabela, rid):
            return self.db.get(tabela, {}).get(str(rid))

        def mock_inserir(tabela, reg):
            self._seq += 1
            rid = str(self._seq)
            self.db.setdefault(tabela, {})[rid] = dict(reg, id=rid)
            return rid

        def mock_alterar(tabela, rid, mud):
            if str(rid) not in self.db.get(tabela, {}):
                raise ixc.IXCError(f"Registro {rid} não encontrado em {tabela}")
            self.db[tabela][str(rid)].update(mud)
            return str(rid)

        self.patchers = [
            patch.object(ixc, "listar", side_effect=mock_listar),
            patch.object(ixc, "buscar", side_effect=mock_buscar),
            patch.object(ixc, "inserir", side_effect=mock_inserir),
            patch.object(ixc, "alterar", side_effect=mock_alterar),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()

    def test_04_listar_contratos_e_cobrancas_status_canonicos(self):
        contratos = financeiro_ixc.listar_contratos_ativos(ixc_cliente_id="10")
        self.assertEqual(len(contratos), 1)
        self.assertEqual(contratos[0]["ixc_contrato_id"], "101")
        self.assertEqual(contratos[0]["valor"], 119.90)

        cobrancas = financeiro_ixc.listar_cobrancas_ixc(ixc_cliente_id="10")
        por_id = {c["identificador_externo"]: c for c in cobrancas}
        self.assertEqual(por_id["501"]["status"], "registrada")
        self.assertEqual(por_id["502"]["status"], "vencida")
        self.assertEqual(por_id["503"]["status"], "paga")
        self.assertEqual(por_id["504"]["status"], "cancelada")
        self.assertEqual(por_id["503"]["data_pagamento"], "2026-09-09")

    def test_05_emissao_boleto_idempotente_no_ixc(self):
        payload = {
            "id_cliente": "10",
            "id_contrato": "101",
            "competencia": "2026-12",
            "data_vencimento": "2026-12-10",
            "valor": 119.90,
            "descricao": "Mensalidade Fibra 600 Mega - 2026-12",
        }
        with patch.object(financeiro_ixc, "obter_pdf_boleto_ixc", return_value={"pdf_base64": base64.b64encode(b"%PDF-1.4 TEST").decode()}):
            r1 = financeiro_ixc.emitir_boleto_ixc(payload)
            self.assertFalse(r1["duplicidade_evitada"])
            self.assertEqual(r1["status"], "registrada")
            self.assertTrue(r1["identificador_externo"])
            qtd_antes = len(self.db["fn_areceber"])

            # Segunda chamada com mesmo (id_cliente, id_contrato, competencia)
            r2 = financeiro_ixc.emitir_boleto_ixc(payload)
            self.assertTrue(r2["duplicidade_evitada"])
            self.assertEqual(r2["identificador_externo"], r1["identificador_externo"])
            self.assertEqual(len(self.db["fn_areceber"]), qtd_antes)

    def test_06_obter_pdf_e_cancelar_boleto(self):
        pdf_bytes = b"%PDF-1.4 fake boleto content"
        b64 = base64.b64encode(pdf_bytes).decode()

        class RespText:
            status_code = 200
            headers = {"content-type": "text/plain"}
            text = b64
            content = b64.encode()
            def json(self):
                raise ValueError("not json")

        class RespJson:
            status_code = 200
            headers = {"content-type": "application/json"}
            text = json.dumps({"base64": b64})
            content = text.encode()
            def json(self):
                return {"base64": b64}

        with patch("financeiro_ixc.requests.post", return_value=RespText()):
            out1 = financeiro_ixc.obter_pdf_boleto_ixc("501")
            self.assertEqual(base64.b64decode(out1["pdf_base64"]), pdf_bytes)

        with patch("financeiro_ixc.requests.post", return_value=RespJson()):
            out2 = financeiro_ixc.obter_pdf_boleto_ixc("501")
            self.assertEqual(base64.b64decode(out2["pdf_base64"]), pdf_bytes)

        canc = financeiro_ixc.cancelar_boleto_ixc("501", motivo="Acordo comercial")
        self.assertEqual(canc["status"], "cancelada")
        self.assertEqual(self.db["fn_areceber"]["501"]["status"], "C")

    def test_07_notas_fiscais_consulta_autorizacao_rejeicao_e_indisponibilidade(self):
        nfs = financeiro_ixc.consultar_notas_fiscais_ixc(ixc_cliente_id="10")
        self.assertEqual(len(nfs), 1)
        self.assertEqual(nfs[0]["status"], "autorizada")
        self.assertEqual(nfs[0]["numero"], "000901")

        # Emissao sem protocolo/chave devolvido pelo IXC -> NUNCA marca autorizada
        res_rej = financeiro_ixc.emitir_nota_fiscal_ixc({
            "id_cliente": "10",
            "id_areceber": "501",
            "tipo_documento": "nfcom",
            "valor": 119.90,
        })
        self.assertNotEqual(res_rej["status"], "autorizada")
        self.assertEqual(res_rej["status"], "rejeitada")

        # Indisponibilidade na API do IXC
        with patch.object(ixc, "inserir", side_effect=ConnectionError("IXC timeout")):
            res_indisp = financeiro_ixc.emitir_nota_fiscal_ixc({
                "id_cliente": "10",
                "id_areceber": "501",
                "tipo_documento": "nfse",
                "valor": 119.90,
            })
            self.assertEqual(res_indisp["status"], "indisponivel")
            self.assertIn("timeout", res_indisp["motivo_falha"])

        # Emissao valida com retorno de autorizacao
        with patch.object(ixc, "inserir", return_value="999"), patch.object(
            ixc,
            "buscar",
            return_value={
                "id": "999",
                "numero_nf": "000999",
                "serie": "1",
                "chave_nf": "35261112345678000190620010000009991000009990",
                "protocolo": "135269999999",
                "status": "A",
            },
        ):
            res_ok = financeiro_ixc.emitir_nota_fiscal_ixc({
                "id_cliente": "10",
                "id_areceber": "501",
                "tipo_documento": "nfcom",
                "valor": 119.90,
            })
            self.assertEqual(res_ok["status"], "autorizada")
            self.assertEqual(res_ok["protocolo"], "135269999999")

    def test_08_rotas_fastapi_autenticacao_e_contratos(self):
        # Token invalido deve lancar 401
        with self.assertRaises(HTTPException) as ctx:
            bridge_main.odoo_fin_contratos(
                bridge_main.FiltroFinanceiro(ixc_cliente_id="10"),
                token="token-errado",
            )
        self.assertEqual(ctx.exception.status_code, 401)

        # Token valido retorna contratos
        resp = bridge_main.odoo_fin_contratos(
            bridge_main.FiltroFinanceiro(ixc_cliente_id="10"),
            token=os.environ["BRIDGE_TOKEN"],
        )
        self.assertIn("contratos", resp)
        self.assertEqual(len(resp["contratos"]), 1)

        # Rota webhook valida token
        with self.assertRaises(HTTPException) as ctx2:
            bridge_main.webhook_financeiro_evento(
                bridge_main.EventoFinanceiroWebhook(evento="boleto_pago", identificador_externo="501"),
                token="errado",
            )
        self.assertEqual(ctx2.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
