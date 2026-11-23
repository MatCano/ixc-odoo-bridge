"""Gestão completa de Notas Fiscais (NFCom Mod. 62 / NFS-e) com XML, PDF e controle de falhas."""
import base64
import random
import time
from datetime import datetime

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .boleto_pdf_engine import (
    gerar_pdf_nota_fiscal,
    gerar_xml_nota_fiscal,
    only_digits,
    validar_cpf_cnpj,
)

P = "maisfibra_financeiro."


def _param(env, key, default=""):
    return env["ir.config_parameter"].sudo().get_param(P + key, default)


def _param_bool(env, key, default=False):
    return _param(env, key, "True" if default else "False") in ("True", "1", "true")


class NotaFiscal(models.Model):
    _name = "maisfibra.nota.fiscal"
    _description = "Nota Fiscal (NFS-e / NFCom)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char("Referência", required=True, copy=False, readonly=True, default="Nova NF")
    partner_id = fields.Many2one("res.partner", "Cliente", required=True, ondelete="restrict", tracking=True, index=True)
    company_id = fields.Many2one("res.company", "Empresa", default=lambda self: self.env.company, required=True)
    cobranca_id = fields.Many2one("maisfibra.cobranca", "Cobrança", ondelete="set null", tracking=True, index=True)
    contrato_id = fields.Many2one("maisfibra.contrato.recorrente", "Contrato", ondelete="set null")
    tipo_documento = fields.Selection(
        [("nfcom", "NFCom (Modelo 62 - SCM)"), ("nfse", "NFS-e (Municipal - SVA)"), ("nfe", "NF-e")],
        string="Tipo de Documento", default="nfcom", required=True, tracking=True,
    )
    origem_emissao = fields.Selection(
        [("odoo", "Odoo (Localização BR / Homologação)"), ("ixc", "IXC Provedor"), ("externo", "Sistema Fiscal Externo")],
        string="Emissor", default="ixc", required=True,
    )
    ambiente = fields.Selection(
        [("homologacao", "Homologação"), ("producao", "Produção")],
        string="Ambiente", default="homologacao", required=True,
    )
    state = fields.Selection(
        [
            ("rascunho", "Rascunho"),
            ("em_processamento", "Em Processamento"),
            ("autorizada", "Autorizada"),
            ("rejeitada", "Rejeitada"),
            ("cancelada", "Cancelada"),
            ("substituida", "Substituída"),
            ("indisponivel", "Serviço Indisponível"),
        ],
        string="Status", default="rascunho", required=True, tracking=True, index=True, copy=False,
    )
    numero = fields.Char("Número NF", readonly=True, copy=False, index=True)
    serie = fields.Char("Série", default="1", copy=False)
    chave_acesso = fields.Char("Chave / Código Verificação", readonly=True, copy=False, index=True)
    protocolo_autorizacao = fields.Char("Protocolo de Autorização", readonly=True, copy=False)
    identificador_externo = fields.Char("ID Externo", copy=False, index=True)
    data_emissao = fields.Datetime("Data/Hora Emissão", readonly=True, copy=False)
    valor_total = fields.Monetary("Valor Total", currency_field="currency_id", required=True)
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id", store=True)
    aliquota_iss = fields.Float("Alíquota ISS (%)", digits=(5, 2))
    valor_iss = fields.Monetary("Valor ISS", currency_field="currency_id")
    aliquota_icms = fields.Float("Alíquota ICMS (%)", digits=(5, 2))
    valor_icms = fields.Monetary("Valor ICMS", currency_field="currency_id")
    descricao_servico = fields.Text("Discriminação dos Serviços")

    xml_arquivo = fields.Binary("XML", attachment=True, copy=False)
    xml_filename = fields.Char("Arquivo XML", copy=False)
    pdf_arquivo = fields.Binary("PDF (DANFE/DANFSe)", attachment=True, copy=False)
    pdf_filename = fields.Char("Arquivo PDF", copy=False)

    motivo_rejeicao = fields.Text("Motivo Rejeição / Falha", readonly=True, copy=False)
    motivo_cancelamento = fields.Text("Motivo Cancelamento", readonly=True, copy=False)
    nf_substituida_id = fields.Many2one("maisfibra.nota.fiscal", "Substitui a NF", readonly=True, copy=False)
    nf_substituta_ids = fields.One2many("maisfibra.nota.fiscal", "nf_substituida_id", "NF Substituta")
    enviada_cliente = fields.Boolean("Enviada ao cliente", default=False, copy=False)
    envio_ids = fields.One2many("maisfibra.cobranca.envio", "nota_fiscal_id", "Envios")
    log_ids = fields.One2many("maisfibra.integracao.log", "nota_fiscal_id", "Logs")
    excecao_ids = fields.One2many("maisfibra.fila.excecao", "nota_fiscal_id", "Exceções")

    @api.model_create_multi
    def create(self, vals_list):
        seq = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == "Nova NF":
                vals["name"] = seq.next_by_code("maisfibra.nota.fiscal") or "NF/NEW"
        return super().create(vals_list)

    def _gerar_chave_44_homologacao(self, numero):
        self.ensure_one()
        uf = "35"
        aamm = fields.Date.today().strftime("%y%m")
        cnpj = only_digits(self.company_id.partner_id.vat or "11222333000181").zfill(14)[:14]
        mod = "62" if self.tipo_documento == "nfcom" else "55"
        serie = only_digits(self.serie or "1").zfill(3)[:3]
        num = only_digits(numero).zfill(9)[-9:]
        tp = "1"
        cod = f"{random.randint(10000000, 99999999):08d}"
        base43 = f"{uf}{aamm}{cnpj}{mod}{serie}{num}{tp}{cod}"
        soma, peso = 0, 2
        for ch in reversed(base43):
            soma += int(ch) * peso
            peso = 2 if peso == 9 else peso + 1
        resto = soma % 11
        dv = 0 if resto in (0, 1) else 11 - resto
        return f"{base43}{dv}"

    def _montar_dados_render(self, numero, chave, protocolo):
        self.ensure_one()
        return {
            "tipo_documento": self.tipo_documento,
            "ambiente": self.ambiente,
            "status": self.state,
            "numero": numero,
            "serie": self.serie or "1",
            "chave_acesso": chave,
            "protocolo": protocolo,
            "data_emissao": fields.Date.today().isoformat(),
            "emitente_nome": self.company_id.name or "Mais Fibra",
            "emitente_doc": self.company_id.partner_id.vat or "",
            "cliente_nome": self.partner_id.name,
            "cliente_doc": self.partner_id.vat or "",
            "cobranca_ref": self.cobranca_id.name if self.cobranca_id else "-",
            "descricao": self.descricao_servico or "Serviços de Telecomunicação / Internet",
            "valor_total": self.valor_total,
            "aliquota_iss": self.aliquota_iss,
            "valor_iss": self.valor_iss,
            "aliquota_icms": self.aliquota_icms,
            "valor_icms": self.valor_icms,
        }

    def _gravar_xml_pdf(self, xml_bytes, pdf_bytes, numero):
        self.ensure_one()
        self.sudo().write({
            "xml_arquivo": base64.b64encode(xml_bytes),
            "xml_filename": f"{self.tipo_documento}_{numero}.xml",
            "pdf_arquivo": base64.b64encode(pdf_bytes),
            "pdf_filename": f"DANFE_{self.tipo_documento}_{numero}.pdf",
        })

    def action_emitir(self):
        IntegracaoLog = self.env["maisfibra.integracao.log"]
        FilaExcecao = self.env["maisfibra.fila.excecao"]

        for rec in self:
            if rec.state == "autorizada":
                continue
            if rec.state in ("cancelada", "substituida"):
                raise UserError(_("Nota fiscal %s está %s e não pode ser emitida.", rec.name, rec.state))

            if rec.ambiente == "producao" and not _param_bool(self.env, "validacao_contabil_confirmada", False):
                raise UserError(_(
                    "Emissão fiscal em ambiente de produção bloqueada até confirmar a validação com a contabilidade "
                    "em Definições > MaisFibra."
                ))

            # Validação obrigatória de CPF/CNPJ e valor antes da transmissão
            if not validar_cpf_cnpj(rec.partner_id.vat):
                msg = _("CPF/CNPJ do destinatário (%s) ausente ou inválido para autorização fiscal.", rec.partner_id.name)
                rec.sudo().write({"state": "rejeitada", "motivo_rejeicao": msg, "protocolo_autorizacao": False})
                IntegracaoLog.registrar("emitir_nf", "falha", nota_fiscal=rec, motivo_falha=msg)
                rec.message_post(body=f"❌ {msg}", subtype_xmlid="mail.mt_note")
                continue

            if rec.valor_total <= 0:
                msg = _("Valor total da Nota Fiscal deve ser maior que zero.")
                rec.sudo().write({"state": "rejeitada", "motivo_rejeicao": msg, "protocolo_autorizacao": False})
                IntegracaoLog.registrar("emitir_nf", "falha", nota_fiscal=rec, motivo_falha=msg)
                continue

            t0 = time.monotonic()
            payload = {
                "id_cliente": rec.partner_id.mf_ixc_cliente_id or "",
                "id_areceber": rec.cobranca_id.identificador_externo if rec.cobranca_id else "",
                "tipo_documento": rec.tipo_documento,
                "valor": float(rec.valor_total),
                "ambiente": rec.ambiente,
            }

            if rec.origem_emissao == "odoo":
                if rec.ambiente == "producao":
                    msg = _("Emissão fiscal em produção requer provedor/emissor autorizado integrado (IXC ou externo).")
                    rec.sudo().write({"state": "rejeitada", "motivo_rejeicao": msg, "protocolo_autorizacao": False})
                    IntegracaoLog.registrar("emitir_nf", "falha", nota_fiscal=rec, payload=payload, motivo_falha=msg)
                    continue

                num = str(rec.id).zfill(6)
                chave = rec._gerar_chave_44_homologacao(num)
                prot = f"135{datetime.now().strftime('%y%m%d%H%M%S')}{rec.id % 100:02d}"
                dados = rec._montar_dados_render(num, chave, prot)
                dados["status"] = "autorizada"
                xml_bytes = gerar_xml_nota_fiscal(dados)
                etree.fromstring(xml_bytes)
                pdf_bytes = gerar_pdf_nota_fiscal(dados)

                rec.sudo().write({
                    "state": "autorizada",
                    "numero": num,
                    "chave_acesso": chave,
                    "protocolo_autorizacao": prot,
                    "identificador_externo": rec.identificador_externo or f"ODOO-NF-{rec.id}",
                    "data_emissao": fields.Datetime.now(),
                    "motivo_rejeicao": False,
                })
                rec._gravar_xml_pdf(xml_bytes, pdf_bytes, num)
                dur = int((time.monotonic() - t0) * 1000)
                IntegracaoLog.registrar(
                    "emitir_nf", "sucesso", identificador_externo=rec.identificador_externo,
                    nota_fiscal=rec, payload=payload, resposta={"numero": num, "protocolo": prot, "chave": chave}, duracao_ms=dur,
                )
                rec.message_post(body=_("✅ NF %s autorizada em homologação (Protocolo %s).", num, prot), subtype_xmlid="mail.mt_note")
            else:
                # Integração via IXC / Emissor externo pelo bridge
                try:
                    resp = self.env["maisfibra.bridge"].call("/odoo/financeiro/notas-fiscais/emitir", payload)
                    dur = int((time.monotonic() - t0) * 1000)
                    st = str(resp.get("status") or "").lower()
                    prot = str(resp.get("protocolo") or "").strip()
                    chave = str(resp.get("chave_acesso") or "").strip()

                    if st == "autorizada" and prot and chave:
                        num = str(resp.get("numero") or rec.id)
                        dados = rec._montar_dados_render(num, chave, prot)
                        dados["status"] = "autorizada"
                        xml_bytes = base64.b64decode(resp["xml_base64"]) if resp.get("xml_base64") else gerar_xml_nota_fiscal(dados)
                        pdf_bytes = base64.b64decode(resp["pdf_base64"]) if resp.get("pdf_base64") else gerar_pdf_nota_fiscal(dados)
                        rec.sudo().write({
                            "state": "autorizada",
                            "numero": num,
                            "serie": str(resp.get("serie") or rec.serie or "1"),
                            "chave_acesso": chave,
                            "protocolo_autorizacao": prot,
                            "identificador_externo": str(resp.get("identificador_externo") or ""),
                            "data_emissao": fields.Datetime.now(),
                            "motivo_rejeicao": False,
                        })
                        rec._gravar_xml_pdf(xml_bytes, pdf_bytes, num)
                        IntegracaoLog.registrar(
                            "emitir_nf", "sucesso", identificador_externo=rec.identificador_externo,
                            nota_fiscal=rec, payload=payload, resposta=resp, duracao_ms=dur,
                        )
                        rec.message_post(body=_("✅ NF %s autorizada (Protocolo %s).", num, prot), subtype_xmlid="mail.mt_note")
                    elif st == "indisponivel":
                        motivo = resp.get("motivo_falha") or "Serviço fiscal temporariamente indisponível."
                        rec.sudo().write({"state": "indisponivel", "motivo_rejeicao": motivo, "protocolo_autorizacao": False})
                        IntegracaoLog.registrar("emitir_nf", "falha", nota_fiscal=rec, payload=payload, resposta=resp, motivo_falha=motivo, duracao_ms=dur)
                        FilaExcecao.enfileirar("emissao_nf", nota_fiscal=rec, motivo_erro=motivo, payload=payload)
                        rec.message_post(body=_("⚠️ Serviço fiscal indisponível: %s", motivo), subtype_xmlid="mail.mt_note")
                    else:
                        motivo = resp.get("motivo_falha") or "Documento não autorizado pelo fisco."
                        rec.sudo().write({"state": "rejeitada", "motivo_rejeicao": motivo, "protocolo_autorizacao": False})
                        IntegracaoLog.registrar("emitir_nf", "falha", nota_fiscal=rec, payload=payload, resposta=resp, motivo_falha=motivo, duracao_ms=dur)
                        rec.message_post(body=_("❌ NF rejeitada: %s", motivo), subtype_xmlid="mail.mt_note")
                except Exception as err:
                    dur = int((time.monotonic() - t0) * 1000)
                    rec.sudo().write({"state": "indisponivel", "motivo_rejeicao": str(err)[:1000], "protocolo_autorizacao": False})
                    IntegracaoLog.registrar("emitir_nf", "falha", nota_fiscal=rec, payload=payload, motivo_falha=str(err), duracao_ms=dur)
                    FilaExcecao.enfileirar("emissao_nf", nota_fiscal=rec, motivo_erro=str(err), payload=payload)

            if rec.state == "autorizada" and _param_bool(self.env, "envio_nf_automatico", False) and not rec.enviada_cliente:
                rec.action_enviar_cliente(tipo_disparo="automatico")

        return True

    def action_enviar_cliente(self, canal=None, tipo_disparo="automatico", forcar=False):
        Envio = self.env["maisfibra.cobranca.envio"].sudo()
        if tipo_disparo == "manual_reenvio":
            if not self.env.is_superuser() and not self.env.user.has_group("maisfibra_financeiro.group_operador_cobranca"):
                raise AccessError(_("Sem permissão para reenviar Nota Fiscal manualmente."))

        for rec in self:
            if rec.state != "autorizada":
                raise UserError(_("Apenas Notas Fiscais autorizadas podem ser enviadas ao cliente."))
            if tipo_disparo == "automatico" and rec.enviada_cliente and not forcar:
                continue

            canal_cfg = canal or _param(self.env, "canal_envio_padrao", "email") or "email"
            canais = ["email", "whatsapp"] if canal_cfg == "ambos" else [canal_cfg]
            algum_ok = False

            for ch in canais:
                if ch == "email":
                    if not rec.partner_id.email:
                        Envio.create({
                            "nota_fiscal_id": rec.id, "cobranca_id": rec.cobranca_id.id if rec.cobranca_id else False,
                            "partner_id": rec.partner_id.id, "canal": "email", "tipo_documento": "nota_fiscal",
                            "tipo_disparo": tipo_disparo, "destinatario": "-", "status": "falha",
                            "mensagem_erro": "Cliente sem e-mail.",
                        })
                        continue
                    att_ids = []
                    if rec.pdf_arquivo:
                        att_ids.append(self.env["ir.attachment"].sudo().create({
                            "name": rec.pdf_filename or f"NF_{rec.numero}.pdf",
                            "datas": rec.pdf_arquivo, "mimetype": "application/pdf",
                            "res_model": rec._name, "res_id": rec.id,
                        }).id)
                    if rec.xml_arquivo:
                        att_ids.append(self.env["ir.attachment"].sudo().create({
                            "name": rec.xml_filename or f"NF_{rec.numero}.xml",
                            "datas": rec.xml_arquivo, "mimetype": "application/xml",
                            "res_model": rec._name, "res_id": rec.id,
                        }).id)
                    tpl = self.env.ref("maisfibra_financeiro.mail_tpl_nota_fiscal", raise_if_not_found=False)
                    if tpl:
                        mid = tpl.sudo().send_mail(rec.id, force_send=False, email_values={"attachment_ids": [(4, a) for a in att_ids]} if att_ids else {})
                    else:
                        mid = self.env["mail.mail"].sudo().create({
                            "subject": f"Nota Fiscal Nº {rec.numero} — Mais Fibra",
                            "email_to": rec.partner_id.email,
                            "body_html": f"<p>Olá {rec.partner_id.name}, segue sua Nota Fiscal Nº {rec.numero} (Chave: {rec.chave_acesso}).</p>",
                            "attachment_ids": [(4, a) for a in att_ids],
                        }).id
                    Envio.create({
                        "nota_fiscal_id": rec.id, "cobranca_id": rec.cobranca_id.id if rec.cobranca_id else False,
                        "partner_id": rec.partner_id.id, "canal": "email", "tipo_documento": "nota_fiscal",
                        "tipo_disparo": tipo_disparo, "destinatario": rec.partner_id.email, "status": "sucesso",
                        "external_id": str(mid),
                    })
                    algum_ok = True
                elif ch == "whatsapp":
                    fone = getattr(rec.partner_id, "phone", False) or getattr(rec.partner_id, "mobile", False)
                    if not fone or "maisfibra.whatsapp.provider" not in self.env:
                        continue
                    try:
                        prov = self.env["maisfibra.whatsapp.provider"].sudo()
                        caption = f"Olá, {rec.partner_id.name}! Segue sua Nota Fiscal Nº {rec.numero} (Valor R$ {rec.valor_total:.2f}). Chave: {rec.chave_acesso}"
                        if rec.pdf_arquivo:
                            eid = prov.send_media(
                                fone, rec.pdf_arquivo.decode() if isinstance(rec.pdf_arquivo, bytes) else rec.pdf_arquivo,
                                "application/pdf", rec.pdf_filename or f"NF_{rec.numero}.pdf", caption=caption,
                            )
                        else:
                            eid = prov.send_text(fone, caption)
                        Envio.create({
                            "nota_fiscal_id": rec.id, "cobranca_id": rec.cobranca_id.id if rec.cobranca_id else False,
                            "partner_id": rec.partner_id.id, "canal": "whatsapp", "tipo_documento": "nota_fiscal",
                            "tipo_disparo": tipo_disparo, "destinatario": fone, "status": "sucesso", "external_id": str(eid or ""),
                        })
                        algum_ok = True
                    except Exception as e:
                        Envio.create({
                            "nota_fiscal_id": rec.id, "cobranca_id": rec.cobranca_id.id if rec.cobranca_id else False,
                            "partner_id": rec.partner_id.id, "canal": "whatsapp", "tipo_documento": "nota_fiscal",
                            "tipo_disparo": tipo_disparo, "destinatario": fone, "status": "falha", "mensagem_erro": str(e)[:500],
                        })

            if algum_ok:
                rec.sudo().write({"enviada_cliente": True})
        return True

    def action_cancelar(self, motivo=""):
        if not self.env.is_superuser() and not self.env.user.has_group("maisfibra_financeiro.group_gestor_financeiro"):
            raise AccessError(_("Apenas o Gestor Financeiro pode cancelar Notas Fiscais."))
        if len((motivo or "").strip()) < 15:
            raise UserError(_("O motivo do cancelamento fiscal deve possuir no mínimo 15 caracteres."))
        for rec in self:
            if rec.state != "autorizada":
                raise UserError(_("Apenas Notas Fiscais autorizadas podem ser canceladas."))
            rec.sudo().write({"state": "cancelada", "motivo_cancelamento": motivo.strip()})
            self.env["maisfibra.integracao.log"].registrar(
                "cancelar_nf", "sucesso", identificador_externo=rec.identificador_externo,
                nota_fiscal=rec, payload={"motivo": motivo.strip()},
            )
            rec.message_post(body=_("🚫 NF cancelada. Motivo: %s", motivo.strip()), subtype_xmlid="mail.mt_note")
        return True

    def action_substituir(self, motivo="", novo_valor=None):
        self.ensure_one()
        if not self.env.is_superuser() and not self.env.user.has_group("maisfibra_financeiro.group_gestor_financeiro"):
            raise AccessError(_("Apenas o Gestor Financeiro pode substituir Notas Fiscais."))
        if self.state != "autorizada":
            raise UserError(_("Apenas Notas Fiscais autorizadas podem ser substituídas."))
        if len((motivo or "").strip()) < 10:
            raise UserError(_("Informe o motivo da substituição (mínimo 10 caracteres)."))

        nova = self.sudo().create({
            "partner_id": self.partner_id.id,
            "company_id": self.company_id.id,
            "cobranca_id": self.cobranca_id.id if self.cobranca_id else False,
            "contrato_id": self.contrato_id.id if self.contrato_id else False,
            "tipo_documento": self.tipo_documento,
            "origem_emissao": self.origem_emissao,
            "ambiente": self.ambiente,
            "valor_total": float(novo_valor) if novo_valor is not None else self.valor_total,
            "aliquota_iss": self.aliquota_iss,
            "aliquota_icms": self.aliquota_icms,
            "descricao_servico": self.descricao_servico or motivo,
            "nf_substituida_id": self.id,
        })
        nova.action_emitir()
        if nova.state == "autorizada":
            self.sudo().write({"state": "substituida", "motivo_cancelamento": f"Substituída por {nova.name}: {motivo}"})
            self.message_post(body=_("🔄 NF substituída pela %s (%s).", nova.name, nova.numero), subtype_xmlid="mail.mt_note")
        return nova

    def action_abrir_wizard_cancelar(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Cancelar Nota Fiscal — %s", self.name),
            "res_model": "maisfibra.cobranca.cancelar.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_alvo": "nf_cancelar", "default_nota_fiscal_id": self.id},
        }

    def action_abrir_wizard_substituir(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Substituir Nota Fiscal — %s", self.name),
            "res_model": "maisfibra.cobranca.cancelar.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_alvo": "nf_substituir",
                "default_nota_fiscal_id": self.id,
                "default_novo_valor": self.valor_total,
            },
        }
