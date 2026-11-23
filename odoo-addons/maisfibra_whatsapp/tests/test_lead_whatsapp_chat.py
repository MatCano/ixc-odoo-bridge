import base64
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLeadWhatsappChat(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, mf_audit_off=True))
        cls.partner = cls.env["res.partner"].create({
            "name": "Cliente Chat Lead TDD",
            "phone": "5511988887777",
        })
        cls.lead = cls.env["crm.lead"].create({
            "name": "[WhatsApp] Cliente Chat Lead TDD",
            "partner_id": cls.partner.id,
            "contact_name": "Cliente Chat Lead TDD",
            "phone": "5511988887777",
            "type": "opportunity",
        })
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param("maisfibra_whatsapp.provider", "chromium")
        ICP.set_param("maisfibra_whatsapp.auto_boas_vindas", "False")
        ICP.set_param("maisfibra_whatsapp.auto_estagio", "False")
        ICP.set_param("maisfibra_whatsapp.bot_ativo", "False")

    def _provider_cls(self):
        return type(self.env["maisfibra.whatsapp.provider"])

    def test_01_historico_completo_cronologico_multicriterio(self):
        Msg = self.env["maisfibra.whatsapp.message"].sudo()
        m1 = Msg.create({
            "lead_id": self.lead.id,
            "partner_id": self.partner.id,
            "phone": "5511988887777",
            "direction": "in",
            "body": "Olá, quero contratar",
            "state": "received",
        })
        m2 = Msg.create({
            "partner_id": self.partner.id,
            "phone": "5511988887777",
            "direction": "out",
            "body": "Resposta espelhada via parceiro",
            "state": "sent",
        })
        m3 = Msg.create({
            "phone": "5511988887777",
            "direction": "in",
            "body": "Mensagem casada só pelo telefone",
            "state": "received",
        })
        # Garante ordenação cronológica crescente explícita por data
        self.env.cr.execute(
            "UPDATE maisfibra_whatsapp_message SET create_date = %s WHERE id = %s",
            ("2026-11-20 10:00:00", m1.id),
        )
        self.env.cr.execute(
            "UPDATE maisfibra_whatsapp_message SET create_date = %s WHERE id = %s",
            ("2026-11-20 10:05:00", m2.id),
        )
        self.env.cr.execute(
            "UPDATE maisfibra_whatsapp_message SET create_date = %s WHERE id = %s",
            ("2026-11-20 10:10:00", m3.id),
        )

        self.lead.invalidate_recordset()
        self.assertGreaterEqual(self.lead.whatsapp_message_count, 3)

        with patch.object(self._provider_cls(), "get_user_session_status", return_value={"status": "connected"}):
            hist = self.lead.get_whatsapp_chat_history()

        self.assertTrue(hist["can_send"])
        self.assertEqual(hist["session_status"], "connected")
        ids = [m["id"] for m in hist["messages"]]
        self.assertEqual(ids[-3:], [m1.id, m2.id, m3.id])
        self.assertEqual([m["body"] for m in hist["messages"][-3:]], [
            "Olá, quero contratar",
            "Resposta espelhada via parceiro",
            "Mensagem casada só pelo telefone",
        ])

    def test_02_process_inbound_from_me_vincula_lead_existente(self):
        Msg = self.env["maisfibra.whatsapp.message"].sudo()
        leads_before = self.env["crm.lead"].search_count([("partner_id", "=", self.partner.id)])

        Msg.process_inbound(
            number="5511988887777",
            text="Enviado pelo celular",
            external_id="EXT_FROM_ME_01",
            from_me=True,
            media_type="image",
            media_url="/media/user_5/teste.webp",
            media_mime="image/webp",
            media_filename="teste.webp",
        )

        leads_after = self.env["crm.lead"].search_count([("partner_id", "=", self.partner.id)])
        self.assertEqual(leads_after, leads_before)

        wa_msg = Msg.search([("external_id", "=", "EXT_FROM_ME_01")], limit=1)
        self.assertTrue(wa_msg)
        self.assertEqual(wa_msg.lead_id.id, self.lead.id)
        self.assertEqual(wa_msg.partner_id.id, self.partner.id)
        self.assertEqual(wa_msg.direction, "out")
        self.assertEqual(wa_msg.media_type, "image")
        self.assertEqual(wa_msg.media_url, "/media/user_5/teste.webp")

    def test_03_envio_texto_direto_do_lead(self):
        with patch.object(self._provider_cls(), "send_text", return_value="WA_OUT_TXT_01") as mock_send:
            res = self.lead.action_send_whatsapp_chat_message(body="Olá, segue o plano!")

        mock_send.assert_called_once_with("5511988887777", "Olá, segue o plano!", user_id=self.env.user.id)
        self.assertTrue(res["ok"])
        msg_data = res["message"]
        self.assertEqual(msg_data["state"], "sent")
        self.assertEqual(msg_data["direction"], "out")
        self.assertEqual(msg_data["body"], "Olá, segue o plano!")
        self.assertEqual(msg_data["external_id"], "WA_OUT_TXT_01")
        self.assertTrue(self.lead.wa_bot_pausado_ate)

        chatter_msgs = self.lead.message_ids.mapped("body")
        self.assertTrue(any("Olá, segue o plano!" in (b or "") for b in chatter_msgs))

    def test_04_envio_midia_direto_do_lead(self):
        raw_png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
        with patch.object(self._provider_cls(), "send_media", return_value="WA_OUT_IMG_01") as mock_media:
            res = self.lead.action_send_whatsapp_chat_message(
                body="Foto da oferta",
                media_base64=raw_png,
                media_mime="image/png",
                media_filename="oferta.png",
            )

        mock_media.assert_called_once()
        self.assertTrue(res["ok"])
        msg_data = res["message"]
        self.assertEqual(msg_data["state"], "sent")
        self.assertEqual(msg_data["media_type"], "image")
        self.assertEqual(msg_data["media_filename"], "oferta.png")
        self.assertTrue(msg_data["media_url"].startswith("/web/content/"))

        wa_msg = self.env["maisfibra.whatsapp.message"].sudo().browse(msg_data["id"])
        self.assertTrue(wa_msg.attachment_id)
        self.assertEqual(msg_data["media_url"], f"/web/content/{wa_msg.attachment_id.id}")

    def test_05_falha_envio_e_reenvio(self):
        with patch.object(self._provider_cls(), "send_text", side_effect=UserError("Sessão desconectada")):
            res_err = self.lead.action_send_whatsapp_chat_message(body="Tentativa offline")

        self.assertFalse(res_err["ok"])
        self.assertIn("Sessão desconectada", res_err["error"])
        self.assertEqual(res_err["message"]["state"], "error")
        msg_id = res_err["message"]["id"]

        with patch.object(self._provider_cls(), "send_text", return_value="WA_RETRY_OK") as mock_retry:
            res_ok = self.lead.action_retry_whatsapp_message(msg_id)

        mock_retry.assert_called_once_with("5511988887777", "Tentativa offline", user_id=self.env.user.id)
        self.assertTrue(res_ok["ok"])
        self.assertEqual(res_ok["message"]["state"], "sent")
        self.assertEqual(res_ok["message"]["external_id"], "WA_RETRY_OK")

    def test_06_validacoes_lead_sem_telefone_e_mensagem_vazia(self):
        lead_sem_fone = self.env["crm.lead"].create({
            "name": "Lead Sem Telefone TDD",
            "type": "opportunity",
        })
        with patch.object(self._provider_cls(), "get_user_session_status", return_value={"status": "disconnected"}):
            hist = lead_sem_fone.get_whatsapp_chat_history()
        self.assertFalse(hist["can_send"])

        with self.assertRaises(UserError):
            lead_sem_fone.action_send_whatsapp_chat_message(body="Oi")

        with self.assertRaises(UserError):
            self.lead.action_send_whatsapp_chat_message(body="   ")

    def test_07_contatos_whatsapp_nao_criam_res_partner_nem_poluem_lista(self):
        Partner = self.env["res.partner"].with_context(active_test=False).sudo()
        Msg = self.env["maisfibra.whatsapp.message"].sudo()

        act_contacts = self.env.ref("contacts.action_contacts")
        self.assertIn("mf_whatsapp_sync", act_contacts.domain or "")

        count_before = Partner.search_count([])
        synced = self.env["maisfibra.whatsapp.provider"].sync_user_contacts(self.env.user.id)
        self.assertEqual(synced, [])
        self.assertEqual(Partner.search_count([]), count_before)

        Msg.process_inbound(
            number="5511977770000",
            text="Lead novo sem criar contato",
            external_id="EXT_NO_PARTNER_01",
            push_name="Desconhecido WA",
            from_me=False,
        )
        self.assertEqual(Partner.search_count([]), count_before)
        lead_novo = self.env["crm.lead"].sudo().search([("phone", "=", "5511977770000")], limit=1)
        self.assertTrue(lead_novo)
        self.assertFalse(lead_novo.partner_id)

        wa_auto = self.env["res.partner"].sudo().create({
            "name": "999999999999",
            "phone": "999999999999",
            "comment": "<p>Contato de WhatsApp (999@lid)</p>",
        })
        normal = self.env["res.partner"].sudo().create({
            "name": "Cliente Plataforma Normal",
            "phone": "5511966665555",
        })
        self.env["res.partner"]._mf_cleanup_whatsapp_synced_contacts()
        wa_auto.invalidate_recordset()
        normal.invalidate_recordset()
        self.assertTrue(wa_auto.mf_whatsapp_sync)
        self.assertFalse(wa_auto.active)
        self.assertFalse(normal.mf_whatsapp_sync)
        self.assertTrue(normal.active)
