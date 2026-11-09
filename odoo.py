"""Acesso ao Odoo via XML-RPC.

Toda escrita vai com context mf_from_bridge=True: assim o módulo maisfibra_crm
NÃO dispara de volta para o IXC quando o próprio bridge move um lead (evita loop).
"""
import logging
import xmlrpc.client

import config

logger = logging.getLogger("IXC-Odoo-Bridge.odoo")
CTX = {"context": {"mf_from_bridge": True}}


class Odoo:
    def __init__(self):
        common = xmlrpc.client.ServerProxy(f"{config.ODOO_URL}/xmlrpc/2/common", allow_none=True)
        self.uid = common.authenticate(config.ODOO_DB, config.ODOO_USER, config.ODOO_PASSWORD, {})
        if not self.uid:
            raise RuntimeError("Falha ao autenticar no Odoo")
        self.models = xmlrpc.client.ServerProxy(f"{config.ODOO_URL}/xmlrpc/2/object", allow_none=True)

    def call(self, model, method, args, kw=None):
        return self.models.execute_kw(
            config.ODOO_DB, self.uid, config.ODOO_PASSWORD, model, method, args, kw or {}
        )

    def search(self, model, domain, limit=0, order=None):
        kw = {"limit": limit} if limit else {}
        if order:
            kw["order"] = order
        return self.call(model, "search", [domain], kw)

    def read(self, model, ids, fields):
        return self.call(model, "read", [ids], {"fields": fields})

    def create(self, model, vals):
        return self.call(model, "create", [vals], CTX)

    def write(self, model, ids, vals):
        return self.call(model, "write", [ids, vals], CTX)

    def stage_por_codigo(self, codigo, team_id=None):
        """Estágio com a 'Regra MaisFibra' = codigo; prefere o do funil do lead."""
        domain = [["mf_code", "=", codigo]]
        if team_id:
            ids = self.search("crm.stage", domain + [["team_ids", "in", [team_id]]], limit=1)
            if ids:
                return ids[0]
        ids = self.search("crm.stage", domain, limit=1, order="sequence")
        return ids[0] if ids else None


_conn = None


def conexao():
    """Reaproveita a conexão; reconecta se a anterior caiu."""
    global _conn
    if _conn is None:
        _conn = Odoo()
    return _conn


def resetar_conexao():
    global _conn
    _conn = None
