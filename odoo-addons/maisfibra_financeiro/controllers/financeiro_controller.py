"""Controllers HTTP, Portal Público de Pagamento e JSON-RPC do Dashboard OWL."""
import base64
import hmac
import json
import logging

from odoo import http
from odoo.http import Response, request

_logger = logging.getLogger(__name__)


class MaisFibraFinanceiroController(http.Controller):
    def _validar_token_webhook(self):
        hdr = request.httprequest.headers.get("X-Webhook-Token") or request.httprequest.headers.get("token") or ""
        ICP = request.env["ir.config_parameter"].sudo()
        esperados = [
            ICP.get_param("maisfibra_financeiro.webhook_token") or "",
            ICP.get_param("maisfibra_crm.bridge_token") or "",
        ]
        esperados = [t for t in esperados if t]
        if not hdr or not esperados:
            return False
        return any(hmac.compare_digest(hdr, esp) for esp in esperados)

    @http.route("/maisfibra/financeiro/webhook", type="http", auth="public", methods=["POST"], csrf=False)
    def financeiro_webhook(self, **kwargs):
        if not self._validar_token_webhook():
            return Response(json.dumps({"error": "unauthorized"}), status=401, content_type="application/json")
        try:
            payload = json.loads(request.httprequest.get_data() or b"{}")
        except ValueError:
            return Response(json.dumps({"error": "invalid json"}), status=400, content_type="application/json")

        res = request.env["maisfibra.cobranca"].sudo().processar_evento_webhook(payload)
        status_code = 200 if res.get("ok") else 422
        return Response(json.dumps(res), status=status_code, content_type="application/json")

    @http.route("/maisfibra/pagamento/<string:token>", type="http", auth="public", methods=["GET"])
    def portal_pagamento(self, token, **kwargs):
        cob = request.env["maisfibra.cobranca"].sudo().search([("access_token", "=", token)], limit=1)
        if not cob:
            return Response("Cobrança não encontrada ou link expirado.", status=404)
        nf_autorizada = cob.nota_fiscal_ids.filtered(lambda n: n.state == "autorizada")[:1]
        return request.render("maisfibra_financeiro.portal_pagamento_page", {
            "cob": cob,
            "nf": nf_autorizada,
            "token": token,
        })

    @http.route("/maisfibra/pagamento/<string:token>/pdf", type="http", auth="public", methods=["GET"])
    def portal_pagamento_pdf(self, token, **kwargs):
        cob = request.env["maisfibra.cobranca"].sudo().search([("access_token", "=", token)], limit=1)
        if not cob:
            return Response("Cobrança não encontrada.", status=404)
        if not cob.boleto_pdf:
            try:
                pdf_bytes = cob._gerar_pdf_local()
                cob._gravar_pdf_attachment(pdf_bytes)
            except Exception as err:
                _logger.warning("Erro gerando PDF no portal para %s: %s", cob.name, err)
                return Response("PDF indisponível.", status=404)
        pdf_raw = base64.b64decode(cob.boleto_pdf)
        fname = cob.boleto_pdf_filename or f"boleto_{cob.name.replace('/', '_')}.pdf"
        return Response(
            pdf_raw,
            status=200,
            headers=[
                ("Content-Type", "application/pdf"),
                ("Content-Disposition", f'inline; filename="{fname}"'),
            ],
        )

    @http.route("/maisfibra/pagamento/<string:token>/nf/pdf", type="http", auth="public", methods=["GET"])
    def portal_pagamento_nf_pdf(self, token, **kwargs):
        cob = request.env["maisfibra.cobranca"].sudo().search([("access_token", "=", token)], limit=1)
        nf = cob.nota_fiscal_ids.filtered(lambda n: n.state == "autorizada" and n.pdf_arquivo)[:1] if cob else False
        if not nf:
            return Response("PDF da Nota Fiscal não disponível.", status=404)
        pdf_raw = base64.b64decode(nf.pdf_arquivo)
        fname = nf.pdf_filename or f"NF_{nf.numero}.pdf"
        return Response(
            pdf_raw,
            status=200,
            headers=[
                ("Content-Type", "application/pdf"),
                ("Content-Disposition", f'inline; filename="{fname}"'),
            ],
        )

    @http.route("/maisfibra/pagamento/<string:token>/nf/xml", type="http", auth="public", methods=["GET"])
    def portal_pagamento_nf_xml(self, token, **kwargs):
        cob = request.env["maisfibra.cobranca"].sudo().search([("access_token", "=", token)], limit=1)
        nf = cob.nota_fiscal_ids.filtered(lambda n: n.state == "autorizada" and n.xml_arquivo)[:1] if cob else False
        if not nf:
            return Response("XML da Nota Fiscal não disponível.", status=404)
        xml_raw = base64.b64decode(nf.xml_arquivo)
        fname = nf.xml_filename or f"NF_{nf.numero}.xml"
        return Response(
            xml_raw,
            status=200,
            headers=[
                ("Content-Type", "application/xml; charset=utf-8"),
                ("Content-Disposition", f'attachment; filename="{fname}"'),
            ],
        )

    @http.route("/maisfibra/financeiro/dashboard_data", type="jsonrpc", auth="user")
    def dashboard_data(self, competencia=None, **kwargs):
        return request.env["maisfibra.cobranca"].get_dashboard_data(competencia=competencia)
