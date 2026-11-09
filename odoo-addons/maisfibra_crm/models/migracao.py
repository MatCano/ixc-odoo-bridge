"""Migração dos vínculos antigos com o IXC.

O bridge antigo guardava o vínculo como TEXTO:
  crm.lead.description  -> "DOC:12345678901; ID_IXC_LEAD:7;"  ou  "DOC:...; ID_IXC_CLI:9;"  ou  "ID_IXC:9;"
  res.partner.comment   -> "ID_IXC_CLI:9;"  ou (bug antigo) "ID_IXC_LEAD:7; ID_IXC_CLI:7;"
Esta rotina copia para os campos mf_* SEM sobrescrever o que já estiver preenchido.
Os campos x_ID_IXC / x_id_IXC / x_status_ixc / x_viabilidade / x_codigo_cliente_ixc
estavam vazios em 09/11/2026 — podem ser arquivados.
"""
import logging
import re

from odoo import api, models

_logger = logging.getLogger(__name__)

RE_DOC = re.compile(r"DOC:(\d{11,14});")
RE_LEAD = re.compile(r"ID_IXC_LEAD:(\d+);")
RE_CLI = re.compile(r"ID_IXC_CLI:(\d+);")
RE_CLI_ANTIGO = re.compile(r"ID_IXC:(\d+);")


def _texto(v):
    return re.sub(r"<[^>]+>", " ", v or "")


def extrair(texto, eh_partner=False):
    """Retorna os campos mf_* encontrados no texto de marcadores."""
    t = _texto(texto)
    vals = {}
    if m := RE_DOC.search(t):
        vals["mf_cpf_cnpj"] = m.group(1)
    lead = RE_LEAD.search(t)
    if lead:
        vals["mf_ixc_contato_id"] = lead.group(1)
    cli = RE_CLI.search(t) or RE_CLI_ANTIGO.search(t)
    # bug antigo: contato do IXC recebia também ID_IXC_CLI com o id do LEAD -> ignorar
    if cli and not (eh_partner and lead and lead.group(1) == cli.group(1)):
        vals["mf_ixc_cliente_id"] = cli.group(1)
    return vals


class MigracaoIxc(models.AbstractModel):
    _name = "maisfibra.migracao"
    _description = "Migração de vínculos antigos com o IXC"

    @api.model
    def migrar(self):
        totais = {"leads": 0, "contatos": 0}
        Lead = self.env["crm.lead"].sudo().with_context(active_test=False, mf_from_bridge=True)
        for lead in Lead.search([("description", "ilike", "ID_IXC")]):
            vals = {k: v for k, v in extrair(lead.description).items() if not lead[k]}
            if vals:
                lead.write(vals)
                totais["leads"] += 1
        Partner = self.env["res.partner"].sudo().with_context(active_test=False, mf_from_bridge=True)
        for p in Partner.search([("comment", "ilike", "ID_IXC")]):
            vals = {k: v for k, v in extrair(p.comment, eh_partner=True).items()
                    if k != "mf_cpf_cnpj" and not p[k]}
            if vals:
                p.write(vals)
                totais["contatos"] += 1
        _logger.info("Migração IXC: %s", totais)
        return totais

    @api.model
    def action_migrar(self):
        t = self.migrar()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Migração concluída",
                "message": f"{t['leads']} leads e {t['contatos']} contatos atualizados com os IDs do IXC.",
                "sticky": True,
                "type": "success",
            },
        }
