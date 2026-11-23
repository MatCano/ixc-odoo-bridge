"""Gestão completa de Cobranças e Boletos (Ciclo de vida, FEBRABAN, PDF, 2ª via, Baixa e Sync IXC)."""
import base64
import logging
import time
import uuid
from datetime import date, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .boleto_pdf_engine import (
    gerar_dados_febraban,
    gerar_pdf_boleto,
    gerar_pix_emv_payload,
    only_digits,
    validar_cpf_cnpj,
)

_logger = logging.getLogger(__name__)
P = "maisfibra_financeiro."


def _param(env, key, default=""):
    return env["ir.config_parameter"].sudo().get_param(P + key, default)


def _param_bool(env, key, default=False):
    return _param(env, key, "True" if default else "False") in ("True", "1", "true")


class Cobranca(models.Model):
    _name = "maisfibra.cobranca"
    _description = "Cobrança e Boleto Bancário"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "data_vencimento desc, id desc"

    name = fields.Char("Número", required=True, copy=False, readonly=True, default="Nova Cobrança")
    partner_id = fields.Many2one("res.partner", "Cliente", required=True, ondelete="restrict", tracking=True, index=True)
    contrato_id = fields.Many2one("maisfibra.contrato.recorrente", "Contrato / Assinatura", ondelete="set null", tracking=True, index=True)
    company_id = fields.Many2one("res.company", "Empresa", default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id", store=True)
    competencia = fields.Char("Competência (AAAA-MM)", required=True, default=lambda self: date.today().strftime("%Y-%m"), index=True)
    descricao = fields.Char("Descrição / Referência", default="Mensalidade Internet Fibra")

    state = fields.Selection(
        [
            ("criada", "Criada"),
            ("registrada", "Registrada / Emitida"),
            ("enviada", "Enviada"),
            ("paga", "Paga"),
            ("vencida", "Vencida"),
            ("cancelada", "Cancelada"),
            ("falha", "Com Falha"),
        ],
        string="Status", default="criada", required=True, tracking=True, index=True, copy=False,
    )
    origem_emissao = fields.Selection(
        [("a_odoo", "Odoo (Banco/FEBRABAN)"), ("b_ixc", "IXC Provedor"), ("c_hibrido", "Híbrido (IXC + Odoo)")],
        string="Modo de Emissão", default=lambda self: _param(self.env, "arquitetura_cobranca", "c_hibrido") or "c_hibrido",
    )

    data_emissao = fields.Date("Data de Emissão", default=fields.Date.today, copy=False)
    data_vencimento = fields.Date("Vencimento", required=True, tracking=True, index=True)
    data_pagamento = fields.Date("Data do Pagamento", readonly=True, tracking=True, copy=False)

    valor_original = fields.Monetary("Valor Original", currency_field="currency_id", required=True, tracking=True)
    valor_desconto = fields.Monetary("Desconto", currency_field="currency_id", default=0.0)
    valor_multa = fields.Monetary("Multa", currency_field="currency_id", default=0.0, copy=False)
    valor_juros = fields.Monetary("Juros", currency_field="currency_id", default=0.0, copy=False)
    valor_liquido = fields.Monetary("Valor Total / Líquido", currency_field="currency_id", compute="_compute_valor_liquido", store=True)
    valor_pago = fields.Monetary("Valor Pago", currency_field="currency_id", readonly=True, copy=False)

    # Dados do boleto / integração
    identificador_externo = fields.Char("ID Externo (IXC/Banco)", index=True, copy=False)
    nosso_numero = fields.Char("Nosso Número", index=True, copy=False)
    linha_digitavel = fields.Char("Linha Digitável", copy=False)
    codigo_barras = fields.Char("Código de Barras", copy=False)
    pix_copia_cola = fields.Text("PIX Copia e Cola", copy=False)
    link_pagamento = fields.Char("Link de Pagamento / 2ª Via", copy=False)
    access_token = fields.Char("Token Público", copy=False, index=True, default=lambda self: str(uuid.uuid4()))

    boleto_pdf = fields.Binary("PDF do Boleto", attachment=True, copy=False)
    boleto_pdf_filename = fields.Char("Nome do PDF", copy=False)
    boleto_attachment_id = fields.Many2one("ir.attachment", "Anexo PDF", copy=False, ondelete="set null")

    chave_idempotencia = fields.Char("Chave de Idempotência", copy=False, index=True)
    cobranca_origem_id = fields.Many2one("maisfibra.cobranca", "1ª Via / Origem", readonly=True, copy=False)
    segunda_via_ids = fields.One2many("maisfibra.cobranca", "cobranca_origem_id", "2ªs Vias")
    numero_via = fields.Integer("Via Nº", default=1, readonly=True, copy=False)

    move_id = fields.Many2one("account.move", "Fatura Odoo (Opcional)", copy=False, ondelete="set null")
    enviada_cliente = fields.Boolean("Enviada ao Cliente", default=False, copy=False)
    ultimo_envio_em = fields.Datetime("Último Envio", readonly=True, copy=False)
    ultimo_erro = fields.Text("Último Erro", readonly=True, copy=False)
    motivo_cancelamento = fields.Text("Motivo Cancelamento", readonly=True, copy=False)

    nota_fiscal_ids = fields.One2many("maisfibra.nota.fiscal", "cobranca_id", "Notas Fiscais")
    envio_ids = fields.One2many("maisfibra.cobranca.envio", "cobranca_id", "Histórico de Envios")
    excecao_ids = fields.One2many("maisfibra.fila.excecao", "cobranca_id", "Fila de Exceções")
    log_ids = fields.One2many("maisfibra.integracao.log", "cobranca_id", "Logs de Integração")

    _chave_idempotencia_uniq = models.Constraint(
        "unique(chave_idempotencia)",
        "Cobrança duplicada para este cliente, contrato e período (chave de idempotência já existente).",
    )

    @api.depends("valor_original", "valor_desconto", "valor_multa", "valor_juros")
    def _compute_valor_liquido(self):
        for rec in self:
            base = max(0.0, float(rec.valor_original or 0.0) - float(rec.valor_desconto or 0.0))
            rec.valor_liquido = round(base + float(rec.valor_multa or 0.0) + float(rec.valor_juros or 0.0), 2)

    @api.model_create_multi
    def create(self, vals_list):
        seq = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == "Nova Cobrança":
                vals["name"] = seq.next_by_code("maisfibra.cobranca") or "COB/NEW"
            if not vals.get("access_token"):
                vals["access_token"] = str(uuid.uuid4())
            if not vals.get("chave_idempotencia"):
                pid = vals.get("partner_id") or 0
                cid = vals.get("contrato_id") or 0
                comp = vals.get("competencia") or date.today().strftime("%Y-%m")
                via = vals.get("numero_via") or 1
                suf = uuid.uuid4().hex[:8] if via > 1 or vals.get("cobranca_origem_id") else f"via{via}"
                vals["chave_idempotencia"] = f"{pid}|{cid}|{comp}|{suf}"
        return super().create(vals_list)

    def calcular_encargos(self, data_referencia=None):
        self.ensure_one()
        ref = fields.Date.to_date(data_referencia) if data_referencia else date.today()
        if not self.data_vencimento or ref <= self.data_vencimento:
            return {"multa": 0.0, "juros": 0.0, "dias_atraso": 0, "total": round(max(0.0, self.valor_original - self.valor_desconto), 2)}

        dias = (ref - self.data_vencimento).days
        multa_pct = self.contrato_id.multa_percentual if self.contrato_id else float(_param(self.env, "multa_percentual", "2.0") or 2.0)
        juros_pct = self.contrato_id.juros_mensal_percentual if self.contrato_id else float(_param(self.env, "juros_mensal_percentual", "1.0") or 1.0)
        base = float(self.valor_original or 0.0)
        multa = round(base * (multa_pct / 100.0), 2)
        juros = round(base * (juros_pct / 100.0) * (dias / 30.0), 2)
        return {"multa": multa, "juros": juros, "dias_atraso": dias, "total": round(base + multa + juros, 2)}

    def _montar_link_portal(self):
        self.ensure_one()
        if not self.access_token:
            self.access_token = str(uuid.uuid4())
        base = self.get_base_url().rstrip("/")
        return f"{base}/maisfibra/pagamento/{self.access_token}"

    def _gravar_pdf_attachment(self, pdf_bytes, filename=None):
        self.ensure_one()
        fname = filename or f"boleto_{self.name.replace('/', '_')}.pdf"
        b64 = base64.b64encode(pdf_bytes)
        att_vals = {
            "name": fname,
            "datas": b64,
            "mimetype": "application/pdf",
            "res_model": self._name,
            "res_id": self.id,
        }
        if self.boleto_attachment_id:
            self.boleto_attachment_id.sudo().write(att_vals)
            att = self.boleto_attachment_id
        else:
            att = self.env["ir.attachment"].sudo().create(att_vals)
        self.sudo().write({
            "boleto_pdf": b64,
            "boleto_pdf_filename": fname,
            "boleto_attachment_id": att.id,
        })
        return att

    def _gerar_pdf_local(self):
        self.ensure_one()
        p = self.partner_id
        comp_p = self.company_id.partner_id
        end_parts = [x for x in (p.street, p.city, p.state_id.code if p.state_id else "", p.zip) if x]
        dados = {
            "numero": self.name,
            "banco_codigo": _param(self.env, "banco_codigo", "756") or "756",
            "banco_nome": _param(self.env, "banco_nome", "SICOOB") or "SICOOB",
            "agencia": _param(self.env, "banco_agencia", "0001") or "0001",
            "conta": _param(self.env, "banco_conta", "0012345") or "0012345",
            "carteira": _param(self.env, "banco_carteira", "109") or "109",
            "linha_digitavel": self.linha_digitavel,
            "codigo_barras": self.codigo_barras,
            "nosso_numero": self.nosso_numero,
            "cedente_nome": self.company_id.name or "Mais Fibra",
            "cedente_doc": comp_p.vat or "",
            "sacado_nome": p.name,
            "sacado_doc": p.vat or "",
            "sacado_endereco": ", ".join(end_parts),
            "contrato_nome": self.contrato_id.plano_nome if self.contrato_id else self.descricao,
            "competencia": self.competencia,
            "data_emissao": str(self.data_emissao or date.today()),
            "vencimento": str(self.data_vencimento),
            "valor": self.valor_liquido or self.valor_original,
            "desconto": self.valor_desconto,
            "pix_copia_cola": self.pix_copia_cola,
            "link_pagamento": self.link_pagamento,
        }
        return gerar_pdf_boleto(dados)

    def action_emitir_boleto(self):
        IntegracaoLog = self.env["maisfibra.integracao.log"]
        FilaExcecao = self.env["maisfibra.fila.excecao"]

        for rec in self:
            if rec.state in ("paga", "cancelada"):
                raise UserError(_("Cobrança %s está %s e não pode ser emitida.", rec.name, rec.state))
            if rec.state in ("registrada", "enviada") and rec.linha_digitavel and rec.boleto_pdf:
                continue

            if rec.partner_id.vat and not validar_cpf_cnpj(rec.partner_id.vat):
                raise UserError(_("CPF/CNPJ do cliente %s é inválido para emissão de boleto.", rec.partner_id.name))

            t0 = time.monotonic()
            modo = rec.origem_emissao or _param(self.env, "arquitetura_cobranca", "c_hibrido") or "c_hibrido"
            usar_ixc = modo == "b_ixc" or (modo == "c_hibrido" and bool(rec.partner_id.mf_ixc_cliente_id))

            payload = {
                "id_cliente": rec.partner_id.mf_ixc_cliente_id or "",
                "id_contrato": rec.contrato_id.ixc_contrato_id if rec.contrato_id else "",
                "competencia": rec.competencia,
                "data_vencimento": str(rec.data_vencimento),
                "valor": float(rec.valor_liquido or rec.valor_original),
                "descricao": rec.descricao or f"Mensalidade {rec.competencia}",
                "banco_codigo": _param(self.env, "banco_codigo", "756") or "756",
            }

            if usar_ixc:
                try:
                    if not rec.partner_id.mf_ixc_cliente_id:
                        raise UserError(_("Cliente %s não possui ID do IXC vinculado para emissão via IXC.", rec.partner_id.name))
                    resp = self.env["maisfibra.bridge"].call("/odoo/financeiro/boletos/emitir", payload)
                    duracao = int((time.monotonic() - t0) * 1000)

                    nn = str(resp.get("nosso_numero") or resp.get("identificador_externo") or rec.id).zfill(11)[-11:]
                    linha = resp.get("linha_digitavel") or ""
                    barra = resp.get("codigo_barras") or ""
                    if not linha or not barra:
                        feb = gerar_dados_febraban(
                            banco=_param(self.env, "banco_codigo", "756"),
                            vencimento=rec.data_vencimento,
                            valor=rec.valor_liquido or rec.valor_original,
                            nosso_numero=nn,
                            agencia=_param(self.env, "banco_agencia", "0001"),
                            conta=_param(self.env, "banco_conta", "0012345"),
                            carteira=_param(self.env, "banco_carteira", "109"),
                        )
                        linha = linha or feb["linha_digitavel"]
                        barra = barra or feb["codigo_barras"]

                    pix_str = resp.get("pix_copia_cola") or gerar_pix_emv_payload(
                        _param(self.env, "pix_chave", rec.company_id.partner_id.vat or "00000000000100"),
                        rec.company_id.name, rec.company_id.city, rec.valor_liquido or rec.valor_original, txid=rec.name,
                    )
                    link_portal = rec._montar_link_portal()
                    link = resp.get("link_pagamento") or link_portal

                    rec.sudo().write({
                        "identificador_externo": str(resp.get("identificador_externo") or ""),
                        "nosso_numero": nn,
                        "linha_digitavel": linha,
                        "codigo_barras": barra,
                        "pix_copia_cola": pix_str,
                        "link_pagamento": link,
                        "data_emissao": rec.data_emissao or date.today(),
                        "state": "registrada",
                        "ultimo_erro": False,
                    })

                    pdf_b64 = resp.get("pdf_base64") or ""
                    pdf_bytes = base64.b64decode(pdf_b64) if pdf_b64 else rec._gerar_pdf_local()
                    rec._gravar_pdf_attachment(pdf_bytes)

                    IntegracaoLog.registrar(
                        "emitir_boleto", "sucesso",
                        identificador_externo=rec.identificador_externo,
                        cobranca=rec, payload=payload, resposta=resp, duracao_ms=duracao,
                    )
                    rec.message_post(body=_("Boleto emitido via IXC (ID %s).", rec.identificador_externo), subtype_xmlid="mail.mt_note")
                except Exception as err:
                    duracao = int((time.monotonic() - t0) * 1000)
                    rec.sudo().write({"state": "falha", "ultimo_erro": str(err)[:1000]})
                    IntegracaoLog.registrar(
                        "emitir_boleto", "falha", cobranca=rec, payload=payload, motivo_falha=str(err), duracao_ms=duracao
                    )
                    FilaExcecao.enfileirar(
                        "emissao_boleto", cobranca=rec, motivo_erro=str(err), payload=payload
                    )
                    rec.message_post(body=_("❌ Falha ao emitir boleto: %s", err), subtype_xmlid="mail.mt_note")
            else:
                # Alternativa A (Odoo FEBRABAN local) ou C Híbrido sem cliente IXC (homologação)
                nn = str(rec.nosso_numero or (only_digits(rec.name) or str(rec.id))).zfill(11)[-11:]
                banco = _param(self.env, "banco_codigo", "756") or "756"
                ag = _param(self.env, "banco_agencia", "0001") or "0001"
                ct = _param(self.env, "banco_conta", "0012345") or "0012345"
                cart = _param(self.env, "banco_carteira", "109") or "109"
                feb = gerar_dados_febraban(
                    banco=banco, vencimento=rec.data_vencimento,
                    valor=rec.valor_liquido or rec.valor_original,
                    nosso_numero=nn, agencia=ag, conta=ct, carteira=cart,
                )
                pix_str = gerar_pix_emv_payload(
                    _param(self.env, "pix_chave", rec.company_id.partner_id.vat or "00000000000100"),
                    rec.company_id.name, rec.company_id.city, rec.valor_liquido or rec.valor_original, txid=rec.name,
                )
                link_portal = rec._montar_link_portal()
                rec.sudo().write({
                    "nosso_numero": feb["nosso_numero"],
                    "codigo_barras": feb["codigo_barras"],
                    "linha_digitavel": feb["linha_digitavel"],
                    "pix_copia_cola": pix_str,
                    "link_pagamento": link_portal,
                    "identificador_externo": rec.identificador_externo or f"ODOO-{rec.id}",
                    "data_emissao": rec.data_emissao or date.today(),
                    "state": "registrada",
                    "ultimo_erro": False,
                })
                pdf_bytes = rec._gerar_pdf_local()
                rec._gravar_pdf_attachment(pdf_bytes)
                duracao = int((time.monotonic() - t0) * 1000)
                IntegracaoLog.registrar(
                    "emitir_boleto", "sucesso", identificador_externo=rec.identificador_externo,
                    cobranca=rec, payload=payload, resposta={"linha_digitavel": feb["linha_digitavel"]}, duracao_ms=duracao,
                )
                rec.message_post(body=_("Boleto emitido (%s) — Nosso Número %s.", modo, rec.nosso_numero), subtype_xmlid="mail.mt_note")

            # Fato gerador fiscal na emissão do boleto
            if rec.state == "registrada" and _param_bool(self.env, "fiscal_ativo", False) and _param(self.env, "fiscal_fato_gerador", "pagamento") == "emissao_boleto":
                rec.action_emitir_nota_fiscal()

        return True

    def action_enviar_cliente(self, canal=None, tipo_disparo="automatico", forcar=False):
        Envio = self.env["maisfibra.cobranca.envio"].sudo()
        FilaExcecao = self.env["maisfibra.fila.excecao"]

        if tipo_disparo == "manual_reenvio":
            if not self.env.is_superuser() and not self.env.user.has_group("maisfibra_financeiro.group_operador_cobranca"):
                raise AccessError(_("Apenas usuários autorizados podem reenviar boletos manualmente."))

        for rec in self:
            if rec.state in ("criada", "falha"):
                rec.action_emitir_boleto()
            if rec.state in ("cancelada", "falha"):
                continue
            if tipo_disparo == "automatico" and rec.enviada_cliente and not forcar:
                continue

            # Verificar consentimento LGPD no contrato (se houver)
            if rec.contrato_id and not rec.contrato_id.consentimento_envio:
                Envio.create({
                    "cobranca_id": rec.id,
                    "partner_id": rec.partner_id.id,
                    "canal": "email",
                    "tipo_documento": "boleto",
                    "tipo_disparo": tipo_disparo,
                    "destinatario": rec.partner_id.email or rec.partner_id.phone or "-",
                    "status": "ignorado",
                    "mensagem_erro": "Envio bloqueado por falta de consentimento do contrato.",
                })
                continue

            canal_cfg = canal
            if not canal_cfg:
                if rec.contrato_id and rec.contrato_id.canal_envio != "herdar":
                    canal_cfg = rec.contrato_id.canal_envio
                else:
                    canal_cfg = _param(self.env, "canal_envio_padrao", "email") or "email"

            canais = ["email", "whatsapp"] if canal_cfg == "ambos" else [canal_cfg]
            algum_sucesso = False

            for ch in canais:
                if ch == "email":
                    if not rec.partner_id.email:
                        Envio.create({
                            "cobranca_id": rec.id, "partner_id": rec.partner_id.id, "canal": "email",
                            "tipo_documento": "boleto", "tipo_disparo": tipo_disparo,
                            "destinatario": "-", "status": "falha", "mensagem_erro": "Cliente sem e-mail cadastrado.",
                        })
                        continue
                    try:
                        tpl = self.env.ref("maisfibra_financeiro.mail_tpl_boleto_cobranca", raise_if_not_found=False)
                        att_ids = [rec.boleto_attachment_id.id] if rec.boleto_attachment_id else []
                        if tpl:
                            mail_id = tpl.sudo().send_mail(
                                rec.id,
                                force_send=False,
                                email_values={"attachment_ids": [(4, aid) for aid in att_ids]} if att_ids else {},
                            )
                        else:
                            mail_id = self.env["mail.mail"].sudo().create({
                                "subject": f"Boleto Mais Fibra - {rec.competencia} ({rec.name})",
                                "email_to": rec.partner_id.email,
                                "body_html": f"<p>Olá {rec.partner_id.name}, segue seu boleto ref. {rec.competencia}.<br/>Linha digitável: <b>{rec.linha_digitavel}</b></p>",
                                "attachment_ids": [(4, aid) for aid in att_ids],
                            }).id
                        Envio.create({
                            "cobranca_id": rec.id, "partner_id": rec.partner_id.id, "canal": "email",
                            "tipo_documento": "boleto", "tipo_disparo": tipo_disparo,
                            "destinatario": rec.partner_id.email, "status": "sucesso", "external_id": str(mail_id),
                        })
                        algum_sucesso = True
                    except Exception as e:
                        Envio.create({
                            "cobranca_id": rec.id, "partner_id": rec.partner_id.id, "canal": "email",
                            "tipo_documento": "boleto", "tipo_disparo": tipo_disparo,
                            "destinatario": rec.partner_id.email, "status": "falha", "mensagem_erro": str(e)[:500],
                        })
                        FilaExcecao.enfileirar("envio_boleto", cobranca=rec, motivo_erro=f"Email: {e}")

                elif ch == "whatsapp":
                    fone = getattr(rec.partner_id, "phone", False) or getattr(rec.partner_id, "mobile", False)
                    if not fone or "maisfibra.whatsapp.provider" not in self.env:
                        msg_err = "Cliente sem telefone." if not fone else "Módulo WhatsApp não disponível."
                        Envio.create({
                            "cobranca_id": rec.id, "partner_id": rec.partner_id.id, "canal": "whatsapp",
                            "tipo_documento": "boleto", "tipo_disparo": tipo_disparo,
                            "destinatario": fone or "-", "status": "falha", "mensagem_erro": msg_err,
                        })
                        continue
                    try:
                        prov = self.env["maisfibra.whatsapp.provider"].sudo()
                        caption = (
                            f"Olá, {rec.partner_id.name}! Segue seu boleto Mais Fibra ({rec.competencia}) "
                            f"no valor de R$ {rec.valor_liquido:.2f}, vencimento {rec.data_vencimento}.\n"
                            f"Linha digitável: {rec.linha_digitavel}\nLink: {rec.link_pagamento or ''}"
                        ).strip()
                        if rec.boleto_pdf:
                            ext_id = prov.send_media(
                                fone,
                                rec.boleto_pdf.decode() if isinstance(rec.boleto_pdf, bytes) else rec.boleto_pdf,
                                "application/pdf",
                                rec.boleto_pdf_filename or "boleto.pdf",
                                caption=caption,
                            )
                        else:
                            ext_id = prov.send_text(fone, caption)
                        Envio.create({
                            "cobranca_id": rec.id, "partner_id": rec.partner_id.id, "canal": "whatsapp",
                            "tipo_documento": "boleto", "tipo_disparo": tipo_disparo,
                            "destinatario": fone, "status": "sucesso", "external_id": str(ext_id or ""),
                        })
                        algum_sucesso = True
                    except Exception as e:
                        Envio.create({
                            "cobranca_id": rec.id, "partner_id": rec.partner_id.id, "canal": "whatsapp",
                            "tipo_documento": "boleto", "tipo_disparo": tipo_disparo,
                            "destinatario": fone, "status": "falha", "mensagem_erro": str(e)[:500],
                        })
                        FilaExcecao.enfileirar("envio_boleto", cobranca=rec, motivo_erro=f"WhatsApp: {e}")

            if algum_sucesso:
                vals_upd = {"enviada_cliente": True, "ultimo_envio_em": fields.Datetime.now()}
                if rec.state == "registrada":
                    vals_upd["state"] = "enviada"
                rec.sudo().write(vals_upd)
                rec.message_post(body=_("📤 Boleto enviado ao cliente (%s).", tipo_disparo), subtype_xmlid="mail.mt_note")

        return True

    def action_reenviar_manual(self):
        return self.action_enviar_cliente(tipo_disparo="manual_reenvio", forcar=True)

    def action_registrar_pagamento(self, valor_pago=None, data_pagamento=None):
        for rec in self:
            if rec.state == "cancelada":
                raise UserError(_("Cobrança cancelada não pode receber baixa de pagamento."))
            if rec.state == "paga":
                continue
            dt_pg = fields.Date.to_date(data_pagamento) if data_pagamento else date.today()
            enc = rec.calcular_encargos(data_referencia=dt_pg)
            val_pg = float(valor_pago) if valor_pago is not None else enc["total"]
            rec.sudo().write({
                "state": "paga",
                "data_pagamento": dt_pg,
                "valor_multa": enc["multa"],
                "valor_juros": enc["juros"],
                "valor_pago": val_pg,
            })
            rec.message_post(body=_("✅ Pagamento confirmado em %s no valor de R$ %.2f.", dt_pg, val_pg), subtype_xmlid="mail.mt_note")

            if _param_bool(self.env, "fiscal_ativo", False) and _param(self.env, "fiscal_fato_gerador", "pagamento") == "pagamento":
                ja_tem_nf = rec.nota_fiscal_ids.filtered(lambda n: n.state in ("autorizada", "em_processamento"))
                if not ja_tem_nf:
                    rec.action_emitir_nota_fiscal()
        return True

    def action_cancelar(self, motivo=""):
        if not self.env.is_superuser() and not self.env.user.has_group("maisfibra_financeiro.group_gestor_financeiro"):
            raise AccessError(_("Apenas o Gestor Financeiro pode cancelar cobranças."))
        for rec in self:
            if rec.state == "paga":
                raise UserError(_("Cobrança %s já está paga e não pode ser cancelada.", rec.name))
            if rec.state == "cancelada":
                continue
            if rec.identificador_externo and not str(rec.identificador_externo).startswith("ODOO-") and rec.origem_emissao in ("b_ixc", "c_hibrido") and _param(self.env, "arquitetura_cobranca", "c_hibrido") != "a_odoo":
                try:
                    self.env["maisfibra.bridge"].call(
                        "/odoo/financeiro/boletos/cancelar",
                        {"identificador_externo": rec.identificador_externo, "motivo": motivo or "Cancelado no Odoo"},
                    )
                except Exception as err:
                    self.env["maisfibra.integracao.log"].registrar(
                        "cancelar_boleto", "falha", identificador_externo=rec.identificador_externo,
                        cobranca=rec, motivo_falha=str(err),
                    )
                    raise UserError(_("Erro ao cancelar boleto no IXC: %s", err)) from err
            rec.sudo().write({"state": "cancelada", "motivo_cancelamento": motivo or "Cancelamento manual"})
            rec.message_post(body=_("🚫 Cobrança cancelada. Motivo: %s", motivo or "-"), subtype_xmlid="mail.mt_note")
        return True

    def action_gerar_segunda_via(self, nova_data_vencimento=None):
        self.ensure_one()
        if not self.env.is_superuser() and not self.env.user.has_group("maisfibra_financeiro.group_operador_cobranca"):
            raise AccessError(_("Sem permissão para gerar 2ª via."))
        if self.state == "paga":
            raise UserError(_("Não é possível gerar 2ª via de uma cobrança já paga."))

        dias = int(_param(self.env, "dias_segunda_via", "5") or 5)
        novo_venc = fields.Date.to_date(nova_data_vencimento) if nova_data_vencimento else (date.today() + timedelta(days=dias))
        enc = self.calcular_encargos(data_referencia=novo_venc)
        novo_valor = round(max(float(self.valor_original), enc["total"]), 2)
        nova_via_num = (self.numero_via or 1) + 1

        nova = self.sudo().create({
            "partner_id": self.partner_id.id,
            "contrato_id": self.contrato_id.id if self.contrato_id else False,
            "company_id": self.company_id.id,
            "competencia": self.competencia,
            "data_vencimento": novo_venc,
            "valor_original": novo_valor,
            "valor_desconto": 0.0,
            "descricao": f"2ª Via ({nova_via_num}ª) — {self.descricao or self.name}",
            "cobranca_origem_id": self.id,
            "numero_via": nova_via_num,
            "origem_emissao": self.origem_emissao,
            "chave_idempotencia": f"{self.chave_idempotencia}|via{nova_via_num}|{uuid.uuid4().hex[:8]}",
        })
        if self.state != "cancelada":
            self.sudo().action_cancelar(f"Substituída pela {nova_via_num}ª via ({nova.name})")
        nova.action_emitir_boleto()
        return nova

    def action_reprocessar(self):
        for rec in self:
            if rec.state == "falha":
                rec.action_emitir_boleto()
        return True

    def action_abrir_wizard_pagamento(self):
        self.ensure_one()
        enc = self.calcular_encargos(data_referencia=date.today())
        return {
            "type": "ir.actions.act_window",
            "name": _("Registrar Pagamento — %s", self.name),
            "res_model": "maisfibra.cobranca.pagamento.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_cobranca_id": self.id,
                "default_data_pagamento": date.today(),
                "default_valor_pago": enc["total"],
            },
        }

    def action_abrir_wizard_cancelar(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Cancelar Cobrança — %s", self.name),
            "res_model": "maisfibra.cobranca.cancelar.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_alvo": "cobranca", "default_cobranca_id": self.id},
        }

    def action_abrir_segunda_via_form(self):
        self.ensure_one()
        nova = self.action_gerar_segunda_via()
        return {
            "type": "ir.actions.act_window",
            "res_model": "maisfibra.cobranca",
            "res_id": nova.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_emitir_nota_fiscal(self):
        self.ensure_one()
        NF = self.env["maisfibra.nota.fiscal"]
        existente = self.nota_fiscal_ids.filtered(lambda n: n.state == "autorizada")
        if existente:
            return existente[:1]

        tipo = _param(self.env, "fiscal_tipo_padrao", "nfcom") or "nfcom"
        ambiente = _param(self.env, "fiscal_ambiente", "homologacao") or "homologacao"
        origem = _param(self.env, "sistema_mestre_fiscal", "ixc") or "ixc"
        val_total = float(self.valor_pago or self.valor_liquido or self.valor_original)
        aliq_iss = float(_param(self.env, "aliquota_iss", "2.0") or 0.0) if tipo == "nfse" else 0.0
        aliq_icms = float(_param(self.env, "aliquota_icms", "0.0") or 0.0) if tipo == "nfcom" else 0.0

        nf = NF.create({
            "partner_id": self.partner_id.id,
            "company_id": self.company_id.id,
            "cobranca_id": self.id,
            "contrato_id": self.contrato_id.id if self.contrato_id else False,
            "tipo_documento": tipo,
            "origem_emissao": origem,
            "ambiente": ambiente,
            "valor_total": val_total,
            "aliquota_iss": aliq_iss,
            "valor_iss": round(val_total * (aliq_iss / 100.0), 2),
            "aliquota_icms": aliq_icms,
            "valor_icms": round(val_total * (aliq_icms / 100.0), 2),
            "descricao_servico": self.descricao or f"Serviço de Internet - Ref. {self.competencia}",
        })
        if hasattr(nf, "action_emitir"):
            nf.action_emitir()
        return nf

    @api.model
    def sincronizar_lote_ixc(self, registros):
        Partner = self.env["res.partner"].sudo()
        Contrato = self.env["maisfibra.contrato.recorrente"].sudo()
        IntegracaoLog = self.env["maisfibra.integracao.log"]

        criadas, atualizadas, ignoradas = 0, 0, 0
        for item in registros or []:
            ext_id = str(item.get("identificador_externo") or "").strip()
            if not ext_id:
                ignoradas += 1
                continue

            cob = self.sudo().search([("identificador_externo", "=", ext_id)], limit=1)
            ixc_cli = str(item.get("ixc_cliente_id") or "").strip()
            ixc_ctr = str(item.get("ixc_contrato_id") or "").strip()
            comp = str(item.get("competencia") or date.today().strftime("%Y-%m"))[:7]

            partner = cob.partner_id if cob else Partner.search([("mf_ixc_cliente_id", "=", ixc_cli)], limit=1)
            if not partner and ixc_cli:
                partner = Partner.create({"name": item.get("cliente_nome") or f"Cliente IXC {ixc_cli}", "mf_ixc_cliente_id": ixc_cli})
            if not partner:
                ignoradas += 1
                continue

            contrato = cob.contrato_id if cob else (Contrato.search([("ixc_contrato_id", "=", ixc_ctr)], limit=1) if ixc_ctr else Contrato)

            if not cob:
                chave = f"{partner.id}|{contrato.id if contrato else 0}|{comp}|via1"
                cob = self.sudo().search([("chave_idempotencia", "=", chave)], limit=1)

            st_ext = item.get("status") or "registrada"
            vals = {
                "identificador_externo": ext_id,
                "linha_digitavel": item.get("linha_digitavel") or (cob.linha_digitavel if cob else False),
                "codigo_barras": item.get("codigo_barras") or (cob.codigo_barras if cob else False),
                "nosso_numero": item.get("nosso_numero") or (cob.nosso_numero if cob else False),
                "pix_copia_cola": item.get("pix_copia_cola") or (cob.pix_copia_cola if cob else False),
                "link_pagamento": item.get("link_pagamento") or (cob.link_pagamento if cob else False),
            }
            if st_ext == "paga":
                vals.update({
                    "state": "paga",
                    "data_pagamento": item.get("data_pagamento") or date.today(),
                    "valor_pago": float(item.get("valor_pago") or item.get("valor") or 0.0),
                })
            elif st_ext == "cancelada":
                vals["state"] = "cancelada"
            elif st_ext == "vencida" and (not cob or cob.state not in ("paga", "cancelada")):
                vals["state"] = "vencida"
            elif not cob or cob.state not in ("enviada", "paga", "cancelada"):
                vals["state"] = st_ext

            if cob:
                cob.sudo().write(vals)
                atualizadas += 1
            else:
                vals.update({
                    "partner_id": partner.id,
                    "contrato_id": contrato.id if contrato else False,
                    "competencia": comp,
                    "data_vencimento": item.get("data_vencimento") or date.today(),
                    "valor_original": float(item.get("valor") or 0.0),
                    "origem_emissao": "b_ixc",
                })
                self.sudo().create(vals)
                criadas += 1

        IntegracaoLog.registrar(
            "sync_lote_ixc", "sucesso", direcao="inbound",
            resposta={"criadas": criadas, "atualizadas": atualizadas, "ignoradas": ignoradas},
        )
        return {"criadas": criadas, "atualizadas": atualizadas, "ignoradas": ignoradas}

    @api.model
    def action_sincronizar_ixc(self, ixc_cliente_id=""):
        resp = self.env["maisfibra.bridge"].call(
            "/odoo/financeiro/cobrancas/listar",
            {"ixc_cliente_id": str(ixc_cliente_id or ""), "rp": 100},
        )
        return self.sincronizar_lote_ixc(resp.get("cobrancas") or [])

    @api.model
    def _cron_atualizar_vencidas(self):
        hoje = date.today()
        vencidas = self.sudo().search([
            ("state", "in", ("registrada", "enviada")),
            ("data_vencimento", "<", hoje),
        ])
        if vencidas:
            vencidas.write({"state": "vencida"})
        return len(vencidas)

    @api.model
    def _cron_sincronizar_ixc(self):
        if not _param_bool(self.env, "sync_ixc_ativa", False):
            return False
        try:
            return self.action_sincronizar_ixc()
        except Exception as err:
            self.env["maisfibra.integracao.log"].registrar("cron_sync_ixc", "falha", motivo_falha=str(err))
            return False

    def action_vincular_fatura_odoo(self):
        """Gera/vincula fatura contábil (account.move) espelhada quando habilitado."""
        Move = self.env["account.move"].sudo()
        for rec in self:
            if rec.move_id:
                continue
            move = Move.create({
                "move_type": "out_invoice",
                "partner_id": rec.partner_id.id,
                "invoice_date": rec.data_emissao or date.today(),
                "invoice_date_due": rec.data_vencimento,
                "ref": rec.name,
                "invoice_line_ids": [(0, 0, {
                    "name": rec.descricao or f"Mensalidade {rec.competencia}",
                    "quantity": 1.0,
                    "price_unit": float(rec.valor_liquido or rec.valor_original),
                })],
            })
            rec.sudo().write({"move_id": move.id})
        return True

    @api.model
    def processar_evento_webhook(self, payload):
        """Processa evento financeiro/fiscal recebido via webhook de forma idempotente."""
        IntegracaoLog = self.env["maisfibra.integracao.log"]
        evento = str((payload or {}).get("evento") or "").strip().lower()
        ext_id = str((payload or {}).get("identificador_externo") or "").strip()

        if not evento or not ext_id:
            IntegracaoLog.registrar("webhook_evento", "falha", direcao="inbound", payload=payload, motivo_falha="Evento ou identificador_externo ausente")
            return {"ok": False, "detail": "evento e identificador_externo são obrigatórios"}

        if evento in ("boleto_pago", "boleto_cancelado", "boleto_vencido"):
            cob = self.sudo().search([("identificador_externo", "=", ext_id)], limit=1)
            if not cob:
                IntegracaoLog.registrar("webhook_evento", "aviso", direcao="inbound", identificador_externo=ext_id, payload=payload, motivo_falha="Cobrança não localizada")
                return {"ok": False, "detail": f"Cobrança {ext_id} não localizada"}

            if evento == "boleto_pago":
                if cob.state != "paga":
                    dt_pg = payload.get("data_pagamento") or date.today()
                    val_pg = payload.get("valor_pago")
                    cob.action_registrar_pagamento(valor_pago=val_pg if val_pg else None, data_pagamento=dt_pg)
            elif evento == "boleto_cancelado":
                if cob.state not in ("cancelada", "paga"):
                    cob.sudo().write({"state": "cancelada", "motivo_cancelamento": payload.get("motivo") or "Cancelado via webhook"})
            elif evento == "boleto_vencido":
                if cob.state in ("registrada", "enviada"):
                    cob.sudo().write({"state": "vencida"})

            IntegracaoLog.registrar("webhook_evento", "sucesso", direcao="inbound", identificador_externo=ext_id, cobranca=cob, payload=payload)
            return {"ok": True, "cobranca_id": cob.id, "state": cob.state}

        if evento in ("nf_autorizada", "nf_rejeitada", "nf_cancelada"):
            NF = self.env["maisfibra.nota.fiscal"].sudo()
            nf = NF.search(["|", ("identificador_externo", "=", ext_id), ("chave_acesso", "=", payload.get("chave_acesso") or ext_id)], limit=1)
            if not nf:
                IntegracaoLog.registrar("webhook_evento", "aviso", direcao="inbound", identificador_externo=ext_id, payload=payload, motivo_falha="NF não localizada")
                return {"ok": False, "detail": f"Nota Fiscal {ext_id} não localizada"}

            if evento == "nf_autorizada" and payload.get("protocolo") and payload.get("chave_acesso"):
                nf.write({
                    "state": "autorizada",
                    "protocolo_autorizacao": payload.get("protocolo"),
                    "chave_acesso": payload.get("chave_acesso"),
                    "motivo_rejeicao": False,
                })
            elif evento == "nf_rejeitada":
                nf.write({
                    "state": "rejeitada",
                    "protocolo_autorizacao": False,
                    "motivo_rejeicao": payload.get("motivo") or "Rejeitada via webhook",
                })
            elif evento == "nf_cancelada":
                nf.write({"state": "cancelada", "motivo_cancelamento": payload.get("motivo") or "Cancelada via webhook"})

            IntegracaoLog.registrar("webhook_evento", "sucesso", direcao="inbound", identificador_externo=ext_id, nota_fiscal=nf, payload=payload)
            return {"ok": True, "nota_fiscal_id": nf.id, "state": nf.state}

        return {"ok": False, "detail": f"Evento não reconhecido: {evento}"}

    @api.model
    def get_dashboard_data(self, competencia=None):
        comp = competencia or date.today().strftime("%Y-%m")
        dom_comp = [("competencia", "=", comp)] if comp and comp != "todas" else []
        todas = self.sudo().search(dom_comp)

        a_receber = sum(c.valor_liquido for c in todas if c.state in ("criada", "registrada", "enviada"))
        recebido = sum(c.valor_pago or c.valor_liquido for c in todas if c.state == "paga")
        vencido = sum(c.valor_liquido for c in todas if c.state == "vencida")
        total_val = a_receber + recebido + vencido
        adimplencia = round((recebido / total_val) * 100, 1) if total_val > 0 else 100.0

        NF = self.env["maisfibra.nota.fiscal"].sudo()
        nfs = NF.search([])
        Excecao = self.env["maisfibra.fila.excecao"].sudo()
        excecoes_pend = Excecao.search([("state", "in", ("pendente", "reprocessando"))], limit=10)
        logs_rec = self.env["maisfibra.integracao.log"].sudo().search([], limit=5)

        contagens = {
            st: len(todas.filtered(lambda x, s=st: x.state == s))
            for st in ("criada", "registrada", "enviada", "paga", "vencida", "cancelada", "falha")
        }

        recentes = []
        for c in todas[:15]:
            recentes.append({
                "id": c.id,
                "name": c.name,
                "partner_name": c.partner_id.name,
                "competencia": c.competencia,
                "data_vencimento": str(c.data_vencimento or ""),
                "valor_liquido": round(c.valor_liquido, 2),
                "state": c.state,
                "tem_pdf": bool(c.boleto_pdf),
                "link_pagamento": c.link_pagamento or "",
                "nf_status": c.nota_fiscal_ids[:1].state if c.nota_fiscal_ids else "",
            })

        excecoes_lista = [{
            "id": e.id,
            "name": e.name,
            "tipo": e.tipo,
            "partner_name": e.partner_id.name or "-",
            "tentativas": e.tentativas,
            "max_tentativas": e.max_tentativas,
            "motivo_erro": (e.motivo_erro or "")[:120],
        } for e in excecoes_pend]

        return {
            "competencia": comp,
            "kpis": {
                "a_receber": round(a_receber, 2),
                "recebido": round(recebido, 2),
                "vencido": round(vencido, 2),
                "taxa_adimplencia": adimplencia,
                "total_cobrancas": len(todas),
                "enviadas": len(todas.filtered("enviada_cliente")),
                "nfs_autorizadas": len(nfs.filtered(lambda n: n.state == "autorizada")),
                "nfs_rejeitadas": len(nfs.filtered(lambda n: n.state in ("rejeitada", "indisponivel"))),
                "excecoes_pendentes": Excecao.search_count([("state", "in", ("pendente", "reprocessando"))]),
            },
            "distribuicao": contagens,
            "recentes": recentes,
            "excecoes": excecoes_lista,
            "saude_logs": [{
                "operacao": l.operacao,
                "status": l.status,
                "data_hora": str(l.data_hora or ""),
            } for l in logs_rec],
            "config": {
                "arquitetura": _param(self.env, "arquitetura_cobranca", "c_hibrido") or "c_hibrido",
                "ambiente_fiscal": _param(self.env, "fiscal_ambiente", "homologacao") or "homologacao",
                "fiscal_ativo": _param_bool(self.env, "fiscal_ativo", False),
            },
        }
