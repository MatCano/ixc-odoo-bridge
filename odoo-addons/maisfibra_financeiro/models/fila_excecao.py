"""Fila de exceções operacionais com retentativas controladas e reprocessamento idempotente."""
import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .boleto_pdf_engine import sanitizar_dict

_logger = logging.getLogger(__name__)


class FilaExcecao(models.Model):
    _name = "maisfibra.fila.excecao"
    _description = "Fila de Exceções Financeiras e Fiscais"
    _order = "id desc"

    name = fields.Char("Código", required=True, copy=False, readonly=True, default="Novo")
    tipo = fields.Selection(
        [
            ("emissao_boleto", "Emissão de Boleto"),
            ("envio_boleto", "Envio de Boleto"),
            ("sync_boleto", "Sincronização de Boleto"),
            ("cancelamento_boleto", "Cancelamento de Boleto"),
            ("emissao_nf", "Emissão de Nota Fiscal"),
            ("envio_nf", "Envio de Nota Fiscal"),
            ("webhook", "Webhook"),
        ],
        string="Tipo de Falha", required=True, index=True,
    )
    cobranca_id = fields.Many2one("maisfibra.cobranca", "Cobrança", ondelete="cascade", index=True)
    nota_fiscal_id = fields.Many2one("maisfibra.nota.fiscal", "Nota Fiscal", ondelete="cascade", index=True)
    partner_id = fields.Many2one("res.partner", "Cliente", ondelete="set null", index=True)
    identificador_externo = fields.Char("ID Externo", index=True)
    state = fields.Selection(
        [
            ("pendente", "Pendente"),
            ("reprocessando", "Reprocessando"),
            ("resolvida", "Resolvida"),
            ("descartada", "Descartada"),
        ],
        string="Status", default="pendente", required=True, index=True,
    )
    tentativas = fields.Integer("Tentativas", default=1)
    max_tentativas = fields.Integer("Máx. tentativas", default=lambda self: int(
        self.env["ir.config_parameter"].sudo().get_param("maisfibra_financeiro.max_retentativas", "5") or 5
    ))
    proxima_tentativa = fields.Datetime("Próxima tentativa", default=lambda self: fields.Datetime.now() + timedelta(minutes=5))
    motivo_erro = fields.Text("Motivo do erro")
    payload_json = fields.Text("Dados (sanitizados)")
    resolvido_por = fields.Many2one("res.users", "Resolvido por", readonly=True)
    resolvido_em = fields.Datetime("Resolvido em", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        seq = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "Novo":
                vals["name"] = seq.next_by_code("maisfibra.fila.excecao") or "EXC/NEW"
        return super().create(vals_list)

    @api.model
    def enfileirar(self, tipo, cobranca=None, nota_fiscal=None, partner=None, identificador_externo="", motivo_erro="", payload=None):
        dom = [("tipo", "=", tipo), ("state", "in", ("pendente", "reprocessando"))]
        if cobranca:
            dom.append(("cobranca_id", "=", cobranca.id))
        if nota_fiscal:
            dom.append(("nota_fiscal_id", "=", nota_fiscal.id))
        if not cobranca and not nota_fiscal and identificador_externo:
            dom.append(("identificador_externo", "=", str(identificador_externo)))

        existente = self.sudo().search(dom, limit=1)
        payload_txt = ""
        if payload:
            try:
                payload_txt = json.dumps(sanitizar_dict(payload), ensure_ascii=False, indent=2)
            except Exception:
                payload_txt = str(payload)[:1000]

        if existente:
            novas_tent = existente.tentativas + 1
            atraso_min = min(60 * 12, 5 * (2 ** min(novas_tent, 6)))
            existente.write({
                "tentativas": novas_tent,
                "motivo_erro": str(motivo_erro or "")[:2000],
                "proxima_tentativa": fields.Datetime.now() + timedelta(minutes=atraso_min),
                "payload_json": payload_txt or existente.payload_json,
                "state": "pendente",
            })
            if novas_tent >= existente.max_tentativas:
                existente._notificar_limite_atingido()
            return existente

        p = partner or (cobranca.partner_id if cobranca else (nota_fiscal.partner_id if nota_fiscal else False))
        return self.sudo().create({
            "tipo": tipo,
            "cobranca_id": cobranca.id if cobranca else False,
            "nota_fiscal_id": nota_fiscal.id if nota_fiscal else False,
            "partner_id": p.id if p else False,
            "identificador_externo": str(identificador_externo or (cobranca.identificador_externo if cobranca else "") or ""),
            "motivo_erro": str(motivo_erro or "")[:2000],
            "payload_json": payload_txt,
            "tentativas": 1,
            "proxima_tentativa": fields.Datetime.now() + timedelta(minutes=5),
        })

    def _notificar_limite_atingido(self):
        self.ensure_one()
        try:
            canal = self.env["discuss.channel"].sudo().search([("name", "=", "Integração IXC")], limit=1)
            if canal:
                canal.message_post(
                    body=_("⚠️ Exceção %s atingiu %s tentativas: %s", self.name, self.tentativas, self.motivo_erro or "-"),
                    subtype_xmlid="mail.mt_comment",
                )
        except Exception as e:
            _logger.warning("Falha ao alertar canal Discuss sobre exceção %s: %s", self.name, e)

    def action_reprocessar(self):
        if not self.env.is_superuser() and not self.env.user.has_group("maisfibra_financeiro.group_operador_cobranca"):
            raise AccessError(_("Sem permissão para reprocessar exceções financeiras."))

        for rec in self:
            if rec.state in ("resolvida", "descartada"):
                continue
            rec.sudo().write({"state": "reprocessando"})
            try:
                ok = False
                if rec.tipo == "emissao_boleto" and rec.cobranca_id:
                    rec.cobranca_id.with_context(mf_reprocessando=True).action_emitir_boleto()
                    ok = rec.cobranca_id.state in ("registrada", "enviada", "paga")
                elif rec.tipo == "envio_boleto" and rec.cobranca_id:
                    rec.cobranca_id.with_context(mf_reprocessando=True).action_enviar_cliente(tipo_disparo="automatico", forcar=True)
                    ok = rec.cobranca_id.state in ("enviada", "paga")
                elif rec.tipo == "cancelamento_boleto" and rec.cobranca_id:
                    rec.cobranca_id.with_context(mf_reprocessando=True).action_cancelar("Reprocessamento de exceção")
                    ok = rec.cobranca_id.state == "cancelada"
                elif rec.tipo == "emissao_nf" and rec.nota_fiscal_id:
                    rec.nota_fiscal_id.with_context(mf_reprocessando=True).action_emitir()
                    ok = rec.nota_fiscal_id.state == "autorizada"
                elif rec.tipo == "envio_nf" and rec.nota_fiscal_id:
                    rec.nota_fiscal_id.with_context(mf_reprocessando=True).action_enviar_cliente(forcar=True)
                    ok = rec.nota_fiscal_id.enviada_cliente

                if ok:
                    rec.sudo().write({
                        "state": "resolvida",
                        "resolvido_por": self.env.uid,
                        "resolvido_em": fields.Datetime.now(),
                    })
                elif rec.state == "reprocessando":
                    rec.sudo().write({"state": "pendente"})
            except Exception as err:
                rec.enfileirar(
                    rec.tipo,
                    cobranca=rec.cobranca_id,
                    nota_fiscal=rec.nota_fiscal_id,
                    motivo_erro=str(err),
                )
        return True

    def action_descartar(self):
        if not self.env.is_superuser() and not self.env.user.has_group("maisfibra_financeiro.group_gestor_financeiro"):
            raise AccessError(_("Apenas o Gestor Financeiro pode descartar exceções."))
        self.write({
            "state": "descartada",
            "resolvido_por": self.env.uid,
            "resolvido_em": fields.Datetime.now(),
        })
        return True

    @api.model
    def _cron_reprocessar_fila(self):
        agora = fields.Datetime.now()
        pendentes = self.sudo().search([
            ("state", "=", "pendente"),
            ("proxima_tentativa", "<=", agora),
        ], limit=50)
        for exc in pendentes:
            if exc.tentativas < exc.max_tentativas:
                exc.action_reprocessar()
