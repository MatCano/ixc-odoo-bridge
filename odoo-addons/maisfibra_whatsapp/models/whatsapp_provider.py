"""Camada de provedor de WhatsApp.

Todo o resto do módulo chama apenas `send_text()` e `normalize_phone()`.
Para trocar Evolution -> Meta Cloud API, implemente `_meta_send_text`
e mude o provedor em Definições > WhatsApp. Nada mais precisa mudar.
"""
import logging
import re

import requests

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

PARAM = "maisfibra_whatsapp."


class WhatsappProvider(models.AbstractModel):
    _name = "maisfibra.whatsapp.provider"
    _description = "Provedor de WhatsApp (camada trocável)"

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

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    @api.model
    def send_text(self, phone, text):
        """Envia texto. Retorna o ID externo da mensagem (ou '')."""
        number = self.normalize_phone(phone)
        if not number:
            raise UserError(_("Número de telefone inválido ou vazio."))
        provider = self._param("provider", "evolution")
        if provider == "evolution":
            return self._evolution_send_text(number, text)
        if provider == "meta":
            return self._meta_send_text(number, text)
        raise UserError(_("Provedor de WhatsApp não configurado."))

    # ------------------------------------------------------------------
    # Evolution API (v2)
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
            _logger.warning("Evolution respondeu %s: %s", resp.status_code, resp.text[:500])
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
            _logger.warning("Meta respondeu %s: %s", resp.status_code, erro)
            if (data.get("error") or {}).get("code") == 131047:
                erro = _("Fora da janela de 24h: a Meta só aceita mensagem de template aprovado. %s", erro)
            raise UserError(_("Meta recusou o envio (%s): %s", resp.status_code, erro))
        return ((data.get("messages") or [{}])[0]).get("id", "")

    @api.model
    def _meta_send_text(self, number, text):
        """Texto livre: só funciona até 24h após a última mensagem do cliente (regra da Meta)."""
        return self._meta_post({
            "messaging_product": "whatsapp", "recipient_type": "individual",
            "to": number, "type": "text", "text": {"preview_url": True, "body": text},
        })

    @api.model
    def send_template(self, phone, template, idioma="pt_BR", parametros=None):
        """Mensagem de template aprovado na Meta (obrigatório para iniciar conversa / régua / CSAT).
        Só existe na Meta; na Evolution levanta erro."""
        if self._param("provider", "evolution") != "meta":
            raise UserError(_("Templates só existem no provedor Meta."))
        number = self.normalize_phone(phone)
        corpo = [{"type": "text", "text": str(p)} for p in (parametros or [])]
        componentes = [{"type": "body", "parameters": corpo}] if corpo else []
        return self._meta_post({
            "messaging_product": "whatsapp", "to": number, "type": "template",
            "template": {"name": template, "language": {"code": idioma}, "components": componentes},
        })
