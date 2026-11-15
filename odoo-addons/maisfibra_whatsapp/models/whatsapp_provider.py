"""Camada de provedor de WhatsApp (WhatsApp Web via Chromium, Evolution API ou Meta Cloud API)."""
import json
import logging
import re
import sqlite3
import requests

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

PARAM = "maisfibra_whatsapp."
DEFAULT_CHROMIUM_URL = "http://host.docker.internal:3001"
SQLITE_DB_PATH = "/var/lib/odoo/whatsapp/whatsapp.db"


class WhatsappProvider(models.AbstractModel):
    _name = "maisfibra.whatsapp.provider"
    _description = "Provedor de WhatsApp"

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    @api.model
    def _param(self, key, default=""):
        return self.env["ir.config_parameter"].sudo().get_param(PARAM + key, default)

    @api.model
    def normalize_phone(self, phone):
        """Retorna só dígitos, no formato internacional brasileiro (55 + DDD + número)."""
        digits = re.sub(r"\D", "", phone or "")
        if not digits:
            return ""
        digits = digits.lstrip("0")
        if len(digits) in (10, 11):  # DDD + número, sem país
            digits = "55" + digits
        return digits

    @api.model
    def _get_sqlite_conn(self):
        try:
            return sqlite3.connect(f"file:{SQLITE_DB_PATH}?mode=ro", uri=True)
        except Exception as e:
            _logger.warning("Falha ao abrir banco SQLite local de WhatsApp: %s", e)
            return None

    # ------------------------------------------------------------------
    # API pública de envio
    # ------------------------------------------------------------------
    @api.model
    def send_text(self, phone, text, user_id=None):
        """Envia texto. Retorna o ID externo da mensagem (ou '')."""
        number = self.normalize_phone(phone)
        if not number:
            raise UserError(_("Número de telefone inválido ou vazio."))
        provider = self._param("provider", "chromium")
        if provider == "chromium":
            return self._chromium_send_text(number, text, user_id=user_id)
        if provider == "evolution":
            return self._evolution_send_text(number, text)
        if provider == "meta":
            return self._meta_send_text(number, text)
        raise UserError(_("Provedor de WhatsApp não configurado."))

    @api.model
    def send_media(self, phone, media_base64, media_mime, media_filename, caption=None, is_voice=False, user_id=None):
        """Envia mídia (áudio/voz, imagem, vídeo ou documento)."""
        number = self.normalize_phone(phone)
        if not number:
            raise UserError(_("Número de telefone inválido ou vazio."))
        provider = self._param("provider", "chromium")
        if provider == "chromium":
            return self._chromium_send_media(
                number, media_base64, media_mime, media_filename,
                caption=caption, is_voice=is_voice, user_id=user_id
            )
        raise UserError(_("Envio de mídia suportado atualmente apenas no provedor WhatsApp Web Chromium."))

    # ------------------------------------------------------------------
    # WhatsApp Web via Chromium (Multi-Sessão por Usuário + SQLite)
    # ------------------------------------------------------------------
    @api.model
    def _chromium_url(self):
        return (self._param("chromium_url") or DEFAULT_CHROMIUM_URL).rstrip("/")

    @api.model
    def get_user_session_status(self, user_id=None):
        uid = user_id or self.env.user.id
        url = f"{self._chromium_url()}/api/session/{uid}/status"
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                return resp.json()
        except requests.RequestException as e:
            _logger.warning("WhatsApp Chromium: falha ao consultar status da sessão: %s", e)

        # Fallback para consulta direta no SQLite local
        conn = self._get_sqlite_conn()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT status, qr_code, phone, push_name FROM whatsapp_sessions WHERE user_id = ?",
                    (f"user_{uid}",)
                )
                row = cur.fetchone()
                if row:
                    return {
                        "userId": f"user_{uid}",
                        "status": row[0],
                        "qrCode": row[1],
                        "phone": row[2],
                        "pushname": row[3]
                    }
            finally:
                conn.close()

        return {"userId": f"user_{uid}", "status": "disconnected", "qrCode": None}

    @api.model
    def start_user_session(self, user_id=None):
        uid = user_id or self.env.user.id
        url = f"{self._chromium_url()}/api/session/{uid}/start"
        try:
            resp = requests.post(url, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            raise UserError(_("Falha ao iniciar sessão do WhatsApp: %s", resp.text))
        except requests.RequestException as e:
            raise UserError(_("Falha de conexão com o serviço WhatsApp Chromium: %s", e)) from e

    @api.model
    def disconnect_user_session(self, user_id=None):
        uid = user_id or self.env.user.id
        url = f"{self._chromium_url()}/api/session/{uid}/disconnect"
        try:
            resp = requests.post(url, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            raise UserError(_("Falha ao desconectar sessão: %s", resp.text))
        except requests.RequestException as e:
            raise UserError(_("Falha de conexão com o serviço WhatsApp Chromium: %s", e)) from e

    @api.model
    def sync_user_contacts(self, user_id=None):
        uid = user_id or self.env.user.id
        url = f"{self._chromium_url()}/api/session/{uid}/sync-contacts"
        contacts = []
        try:
            resp = requests.post(url, timeout=15)
            if resp.status_code == 200:
                contacts = resp.json()
        except requests.RequestException as e:
            _logger.warning("Falha ao chamar sync-contacts via HTTP: %s", e)

        if not contacts:
            conn = self._get_sqlite_conn()
            if conn:
                try:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT jid, number, name, push_name, unread_count FROM whatsapp_contacts WHERE user_id = ?",
                        (f"user_{uid}",)
                    )
                    rows = cur.fetchall()
                    contacts = [
                        {"jid": r[0], "number": r[1], "name": r[2], "push_name": r[3], "unread_count": r[4]}
                        for r in rows
                    ]
                finally:
                    conn.close()

        # Garante que cada contato exista como res.partner e tenha canal direto no Discuss
        user = self.env["res.users"].browse(uid)
        Partner = self.env["res.partner"].sudo()
        Channel = self.env["discuss.channel"].sudo()

        saved_contacts = []
        for c in contacts:
            jid = c.get("jid") or ""
            # Ignora grupos, status e newsletters
            if "@g.us" in jid or "@newsletter" in jid or "status@broadcast" in jid:
                continue

            number = self.normalize_phone(c.get("number") or "")
            # Se for LID com mais de 13 digitos ou sem DDI 55, tenta resolver
            if not number or len(number) > 13 or (not number.startswith("55") and len(number) >= 12):
                continue

            name = (c.get("name") or c.get("push_name") or "").strip()
            # Ignora nomes que são IDs técnicos (@lid ou @c.us)
            if "@" in name or name.startswith("user_"):
                name = ""

            partner = self.env["maisfibra.whatsapp.message"]._find_partner(number)
            if not partner:
                final_name = name or number
                partner = Partner.create({
                    "name": final_name,
                    "phone": number,
                    "comment": f"Contato sincronizado do WhatsApp ({jid})"
                })
            elif name and partner.name == number:
                partner.write({"name": name})

            # Cria canal de chat direto em Discuss para o usuário
            try:
                chat = Channel.with_user(user)._get_or_create_chat(partners_to=[partner.id], pin=True)
            except Exception as ex:
                _logger.warning("Erro ao criar canal de chat em Discuss para %s: %s", partner.name, ex)

            saved_contacts.append(c)

        return saved_contacts

    @api.model
    def _chromium_send_text(self, number, text, user_id=None):
        uid = user_id or self.env.user.id
        url = f"{self._chromium_url()}/api/session/{uid}/send"
        payload = {"to": number, "message": text}
        try:
            resp = requests.post(url, json=payload, timeout=20)
        except requests.RequestException as e:
            raise UserError(_("Falha de conexão com o serviço WhatsApp Chromium: %s", e)) from e

        if resp.status_code >= 300:
            raise UserError(_("WhatsApp Chromium recusou o envio (%s): %s", resp.status_code, resp.text[:300]))

        data = resp.json()
        return data.get("messageId", "")

    @api.model
    def _chromium_send_media(self, number, media_base64, media_mime, media_filename, caption=None, is_voice=False, user_id=None):
        uid = user_id or self.env.user.id
        url = f"{self._chromium_url()}/api/session/{uid}/send"
        payload = {
            "to": number,
            "message": caption or "",
            "mediaBase64": media_base64,
            "mediaMime": media_mime,
            "mediaFilename": media_filename,
            "isVoice": is_voice
        }
        try:
            resp = requests.post(url, json=payload, timeout=30)
        except requests.RequestException as e:
            raise UserError(_("Falha de conexão com o serviço WhatsApp Chromium: %s", e)) from e

        if resp.status_code >= 300:
            raise UserError(_("WhatsApp Chromium recusou o envio de mídia (%s): %s", resp.status_code, resp.text[:300]))

        data = resp.json()
        return data.get("messageId", "")

    # ------------------------------------------------------------------
    # Evolution API (legado mantido para compatibilidade se selecionado)
    # ------------------------------------------------------------------
    @api.model
    def _evolution_send_text(self, number, text):
        url = self._param("evolution_url").rstrip("/")
        apikey = self._param("evolution_apikey")
        instance = self._param("evolution_instance")
        if not (url and apikey and instance):
            raise UserError(_("Configure URL, API Key e Instância da Evolution em Definições > WhatsApp."))
        try:
            resp = requests.post(
                f"{url}/message/sendText/{instance}",
                json={"number": number, "text": text},
                headers={"apikey": apikey, "Content-Type": "application/json"},
                timeout=20,
            )
        except requests.RequestException as e:
            raise UserError(_("Falha de conexão com a Evolution API: %s", e)) from e
        if resp.status_code >= 300:
            raise UserError(_("Evolution API recusou o envio (%s): %s", resp.status_code, resp.text[:300]))
        try:
            return (resp.json().get("key") or {}).get("id", "")
        except ValueError:
            return ""

    # ------------------------------------------------------------------
    # Meta Cloud API (WhatsApp Business oficial)
    # ------------------------------------------------------------------
    @api.model
    def _meta_post(self, payload):
        token = self._param("meta_token")
        phone_id = self._param("meta_phone_number_id")
        versao = self._param("meta_api_version", "v21.0") or "v21.0"
        if not (token and phone_id):
            raise UserError(_("Configure o Phone Number ID e o token da Meta em Definições > WhatsApp."))
        try:
            resp = requests.post(
                f"https://graph.facebook.com/{versao}/{phone_id}/messages",
                json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=20,
            )
        except requests.RequestException as e:
            raise UserError(_("Falha de conexão com a Meta: %s", e)) from e
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 300:
            erro = (data.get("error") or {}).get("message") or resp.text[:300]
            raise UserError(_("Meta recusou o envio (%s): %s", resp.status_code, erro))
        return ((data.get("messages") or [{}])[0]).get("id", "")

    @api.model
    def _meta_send_text(self, number, text):
        return self._meta_post({
            "messaging_product": "whatsapp", "recipient_type": "individual",
            "to": number, "type": "text", "text": {"preview_url": True, "body": text},
        })
