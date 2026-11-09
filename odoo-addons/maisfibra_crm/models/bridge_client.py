"""Cliente HTTP do Odoo -> ixc-odoo-bridge.

O Odoo NÃO fala direto com o IXC: ele chama o bridge, que concentra as
credenciais e o conhecimento da API do IXC. Configuração em Definições > MaisFibra.
"""
import logging

import requests

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BridgeClient(models.AbstractModel):
    _name = "maisfibra.bridge"
    _description = "Cliente do ixc-odoo-bridge"

    @api.model
    def call(self, path, payload):
        ICP = self.env["ir.config_parameter"].sudo()
        url = (ICP.get_param("maisfibra_crm.bridge_url") or "").rstrip("/")
        token = ICP.get_param("maisfibra_crm.bridge_token") or ""
        if not url or not token:
            raise UserError(_("Configure URL e token do bridge em Definições > MaisFibra."))
        try:
            resp = requests.post(
                f"{url}{path}", json=payload,
                headers={"token": token, "Content-Type": "application/json"},
                timeout=45,
            )
        except requests.RequestException as e:
            raise UserError(_("Não foi possível falar com o bridge IXC: %s", e)) from e
        try:
            data = resp.json()
        except ValueError:
            data = {"detail": resp.text[:300]}
        if resp.status_code >= 300:
            detail = data.get("detail") or data
            _logger.warning("Bridge %s -> %s: %s", path, resp.status_code, detail)
            raise UserError(_("IXC recusou a operação: %s", detail))
        return data
