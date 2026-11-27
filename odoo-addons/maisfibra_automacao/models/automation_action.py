"""Biblioteca de ações desacopladas, renderização segura de variáveis e sanitização de logs."""
import json
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

ALLOWED_UPDATE_FIELDS = {
    "crm.lead": {
        "name", "contact_name", "partner_name", "email_from", "phone", "mobile",
        "expected_revenue", "priority", "probability", "description", "date_deadline",
        "street", "street2", "city", "zip", "mf_cpf_cnpj", "mf_origem", "mf_concorrente",
        "mf_descricao_produto", "mf_bloco", "mf_apartamento",
    },
    "res.partner": {
        "name", "email", "phone", "mobile", "street", "street2", "city", "zip",
        "comment", "vat", "website", "function",
    },
}

SENSITIVE_KEYS = {"password", "passwd", "secret", "token", "apikey", "api_key", "authorization", "cookie"}
EMAIL_RE = re.compile(r"([a-zA-Z0-9._%+-]{2})[a-zA-Z0-9._%+-]*(@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})")
CPF_CNPJ_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b|\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
PHONE_RE = re.compile(r"\b(?:\+?55)?\s*\(?\d{2}\)?\s*9?\d{4}-?\d{4}\b")
VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


class AutomationTransientError(Exception):
    """Erro transitório elegível para retentativa com backoff exponencial."""


class AutomationActionRunner(models.AbstractModel):
    _name = "mf.automation.action.runner"
    _description = "Executor da Biblioteca de Ações da Automação CRM"

    @api.model
    def sanitize_text(self, text):
        if not text:
            return ""
        s = str(text)
        s = EMAIL_RE.sub(r"\1***\2", s)
        s = CPF_CNPJ_RE.sub("***.***.***-**", s)
        s = PHONE_RE.sub("(XX) XXXXX-****", s)
        return s

    @api.model
    def sanitize_for_log(self, value):
        if value is None or value is False:
            return value
        if isinstance(value, dict):
            cleaned = {}
            for k, v in value.items():
                if any(sk in str(k).lower() for sk in SENSITIVE_KEYS):
                    cleaned[k] = "***REDACTED***"
                else:
                    cleaned[k] = self.sanitize_for_log(v)
            return cleaned
        if isinstance(value, (list, tuple)):
            return [self.sanitize_for_log(item) for item in value]
        if isinstance(value, str):
            return self.sanitize_text(value)
        return value

    @api.model
    def _build_template_context(self, record, cfg=None, extra_context=None):
        cfg = cfg or {}
        extra = extra_context or {}
        is_lead = record._name == "crm.lead"
        partner = record.partner_id if is_lead else record
        company_name = ""
        if "company_id" in record._fields and record.company_id:
            company_name = record.company_id.name or ""
        elif partner and partner.parent_id:
            company_name = partner.parent_id.name or ""
        elif is_lead and record.partner_name:
            company_name = record.partner_name
        else:
            company_name = self.env.company.name or ""

        owner_name = (
            record.user_id.name
            if ("user_id" in record._fields and record.user_id)
            else ""
        )
        contact_name = (
            (partner.name if partner else "")
            or (record.contact_name if is_lead else "")
            or record.name
            or ""
        )
        phone = ""
        if hasattr(record, "_whatsapp_phone"):
            phone = record._whatsapp_phone() or ""
        elif "phone" in record._fields:
            phone = record.phone or ""

        email = ""
        if is_lead:
            email = record.email_from or (partner.email if partner else "") or ""
        elif "email" in record._fields:
            email = record.email or ""

        stage_name = record.stage_id.name if (is_lead and record.stage_id) else ""
        expected_revenue = f"{float(record.expected_revenue or 0.0):.2f}" if is_lead else "0.00"

        return {
            "contact_name": contact_name,
            "company_name": company_name,
            "owner_name": owner_name,
            "lead_name": record.name if is_lead else (record.name or ""),
            "expected_revenue": expected_revenue,
            "stage_name": stage_name,
            "phone": phone,
            "email": email,
            "custom_link": cfg.get("custom_link") or "",
            "_payload": extra.get("payload") or {},
        }

    @api.model
    def render_template(self, template_text, record, cfg=None, extra_context=None):
        cfg = cfg or {}
        if not template_text:
            return ""
        ctx = self._build_template_context(record, cfg=cfg, extra_context=extra_context)
        empty_policy = cfg.get("empty_var_policy") or "blank"
        default_fallback = cfg.get("empty_var_default") or ""
        evaluator = self.env["mf.automation.evaluator"]

        def _replace(match):
            var_name = match.group(1)
            val = ""
            if var_name in ctx:
                val = ctx[var_name]
            elif var_name.startswith("field."):
                field_path = var_name.split(".", 1)[1]
                resolved = evaluator._resolve_field_value(record, field_path, extra_context=extra_context)
                if isinstance(resolved, models.BaseModel):
                    val = resolved.display_name or ""
                elif resolved not in (None, False):
                    val = str(resolved)
            elif var_name.startswith("payload."):
                key = var_name.split(".", 1)[1]
                val = ctx["_payload"].get(key, "")
            else:
                raise UserError(_("Variável não autorizada no template: {{%s}}.", var_name))

            text_val = str(val or "").strip()
            if not text_val:
                if empty_policy == "abort":
                    raise UserError(_("Variável obrigatória vazia durante renderização: {{%s}}.", var_name))
                if empty_policy == "default_text":
                    return default_fallback
                return ""
            return text_val

        return VAR_RE.sub(_replace, template_text)

    @api.model
    def _adapter_send_email(self, record, cfg, rendered_subject, rendered_body, idempotency_key):
        tpl_id = int(cfg.get("template_id") or 0)
        if tpl_id:
            tpl = self.env["mail.template"].sudo().browse(tpl_id).exists()
            if not tpl:
                raise UserError(_("Template de e-mail #%s não encontrado.", tpl_id))
            mail_id = tpl.send_mail(record.id, force_send=False)
            record.message_post(
                body=_("📧 E-mail enfileirado via template <b>%s</b>.", tpl.name),
                subtype_xmlid="mail.mt_note",
            )
            return {"channel": "email", "mail_id": mail_id, "template_id": tpl_id}

        email_to = (
            (record.email_from if record._name == "crm.lead" else record.email)
            or (record.partner_id.email if "partner_id" in record._fields and record.partner_id else "")
            or cfg.get("fallback_email")
            or ""
        ).strip()
        if not email_to:
            raise UserError(_("Registro '%s' não possui e-mail para envio.", record.display_name))

        mail = self.env["mail.mail"].sudo().create({
            "subject": rendered_subject or _("Contato MaisFibra: %s", record.display_name),
            "email_to": email_to,
            "body_html": f"<p>{rendered_body.replace(chr(10), '<br/>')}</p>",
            "model": record._name,
            "res_id": record.id,
        })
        record.message_post(
            body=_("📧 E-mail preparado para %(to)s: <b>%(subj)s</b>", to=self.sanitize_text(email_to), subj=rendered_subject or ""),
            subtype_xmlid="mail.mt_note",
        )
        return {"channel": "email", "mail_id": mail.id, "email_to": email_to}

    @api.model
    def _adapter_send_whatsapp(self, record, cfg, rendered_body, idempotency_key):
        Message = self.env["maisfibra.whatsapp.message"].sudo()
        existing = Message.search([("external_id", "=", idempotency_key)], limit=1)
        if existing:
            return {"channel": "whatsapp", "deduplicated": True, "message_id": existing.id}

        provider = self.env["maisfibra.whatsapp.provider"]
        raw_phone = record._whatsapp_phone() if hasattr(record, "_whatsapp_phone") else (getattr(record, "phone", "") or "")
        number = provider.normalize_phone(raw_phone)
        if not number:
            raise UserError(_("Registro '%s' não possui telefone válido para WhatsApp.", record.display_name))

        try:
            ext_id = provider.send_text(number, rendered_body, user_id=self.env.user.id)
        except Exception as exc:  # noqa: BLE001
            # Falhas de transporte/indisponibilidade do serviço WhatsApp são transitórias
            raise AutomationTransientError(str(exc)) from exc

        is_lead = record._name == "crm.lead"
        partner = record.partner_id if is_lead else record
        wa_msg = Message.create({
            "lead_id": record.id if is_lead else False,
            "partner_id": partner.id if partner else False,
            "phone": number,
            "direction": "out",
            "body": rendered_body,
            "state": "sent",
            "external_id": ext_id or idempotency_key,
            "user_id": self.env.user.id,
            "origem": "auto",
        })
        Message._post_on_record(record, "out", rendered_body, "Automação CRM")
        Message._notify_chat_bus(wa_msg)
        return {"channel": "whatsapp", "message_id": wa_msg.id, "external_id": wa_msg.external_id}

    @api.model
    def _adapter_call_webhook(self, record, cfg, idempotency_key, extra_context=None):
        url = (cfg.get("url") or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            raise UserError(_("URL de webhook inválida: %s", url))
        body_payload = {
            "idempotency_key": idempotency_key,
            "model": record._name,
            "record_id": record.id,
            "record_name": record.display_name,
            "stage_id": record.stage_id.id if ("stage_id" in record._fields and record.stage_id) else False,
            "extra": cfg.get("payload") or {},
        }
        headers = {"Content-Type": "application/json", "X-Idempotency-Key": idempotency_key}
        try:
            resp = requests.post(url, json=body_payload, headers=headers, timeout=10)
        except requests.RequestException as exc:
            raise AutomationTransientError(_("Falha de conexão no webhook: %s", exc)) from exc

        if resp.status_code >= 500 or resp.status_code == 429:
            raise AutomationTransientError(_("Webhook retornou HTTP %s", resp.status_code))
        if resp.status_code >= 400:
            raise UserError(_("Webhook recusou solicitação (HTTP %s): %s", resp.status_code, resp.text[:200]))
        return {"status_code": resp.status_code, "url": url}

    @api.model
    def _compute_next_window_dt(self, now_utc, cfg):
        tz_name = cfg.get("timezone") or self.env.user.tz or "America/Sao_Paulo"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        utc = ZoneInfo("UTC")
        local_dt = now_utc.replace(tzinfo=utc).astimezone(tz)
        allowed_weekdays = cfg.get("weekdays")
        if allowed_weekdays is None:
            allowed_weekdays = [0, 1, 2, 3, 4]  # Seg-Sex padrão
        start_hour = int(cfg.get("window_start_hour") if cfg.get("window_start_hour") is not None else 8)
        end_hour = int(cfg.get("window_end_hour") if cfg.get("window_end_hour") is not None else 18)

        candidate = local_dt
        for _ in range(14):
            if candidate.weekday() in allowed_weekdays:
                if start_hour <= candidate.hour < end_hour:
                    return candidate.astimezone(utc).replace(tzinfo=None)
                if candidate.hour < start_hour:
                    target_local = candidate.replace(hour=start_hour, minute=0, second=0, microsecond=0)
                    return target_local.astimezone(utc).replace(tzinfo=None)
            candidate = (candidate + timedelta(days=1)).replace(hour=start_hour, minute=0, second=0, microsecond=0)
        return now_utc + timedelta(hours=1)

    @api.model
    def execute_node(self, execution, node_dict, record, extra_context=None):
        ntype = node_dict.get("node_type")
        category = node_dict.get("category")
        cfg = node_dict.get("config") or {}
        node_key = node_dict.get("node_key") or "node"
        idempotency_key = f"mf_auto_{execution.id}_{node_key}_v{execution.version_id.version_number if execution.version_id else 0}"
        engine_ctx = {
            "mf_automation_engine": True,
            "mf_automation_source_id": execution.automation_id.id,
            "mf_automation_depth": execution.depth + 1,
        }
        rec_sudo = record.sudo().with_context(**engine_ctx)

        if category == "trigger":
            return {"status": "ok", "branch": "default", "message": _("Gatilho iniciado")}

        if ntype == "end_flow":
            return {"status": "end", "branch": "default", "message": _("Fluxo encerrado pelo bloco de controle.")}

        if ntype == "condition_if":
            rules = cfg.get("rules") or {"logic": "AND", "rules": []}
            matched = self.env["mf.automation.evaluator"].evaluate(record, rules, extra_context=extra_context)
            return {
                "status": "ok",
                "branch": "true" if matched else "false",
                "message": _("Condição avaliada como %s.", "SIM" if matched else "NÃO"),
            }

        if ntype == "segment_check":
            seg_id = int(cfg.get("segment_id") or 0)
            seg = self.env["mf.automation.segment"].sudo().browse(seg_id).exists()
            if not seg:
                raise UserError(_("Segmento #%s não encontrado.", seg_id))
            matched = seg.evaluate_record(record, extra_context=extra_context)
            return {
                "status": "ok",
                "branch": "true" if matched else "false",
                "message": _("Segmento '%s': %s.", seg.name, "SIM" if matched else "NÃO"),
            }

        # Esperas e agendamentos (Seção 7)
        if category == "wait":
            now = fields.Datetime.now()
            timeout_min = int(cfg.get("timeout_minutes") or 0)
            timeout_at = now + timedelta(minutes=timeout_min) if timeout_min > 0 else False

            if ntype == "wait_duration":
                amount = float(cfg.get("amount") or 1)
                unit = cfg.get("unit") or "minutes"
                delta_map = {
                    "minutes": timedelta(minutes=amount),
                    "hours": timedelta(hours=amount),
                    "days": timedelta(days=amount),
                }
                wait_until = now + delta_map.get(unit, timedelta(minutes=amount))
                return {
                    "status": "wait",
                    "wait_spec": {
                        "wait_type": "duration",
                        "wait_until": wait_until,
                        "next_run_at": wait_until,
                        "timeout_at": False,
                        "wait_config": cfg,
                    },
                    "message": _("Aguardando %s %s até %s.", amount, unit, wait_until),
                }

            if ntype == "wait_until_date":
                dt_target = self.env["mf.automation.evaluator"]._to_datetime(cfg.get("target_datetime"))
                if not dt_target:
                    raise UserError(_("Data alvo inválida para espera."))
                return {
                    "status": "wait",
                    "wait_spec": {
                        "wait_type": "until_date",
                        "wait_until": dt_target,
                        "next_run_at": dt_target,
                        "timeout_at": timeout_at,
                        "wait_config": cfg,
                    },
                    "message": _("Aguardando data/horário %s.", dt_target),
                }

            if ntype == "wait_time_window":
                next_dt = self._compute_next_window_dt(now, cfg)
                if next_dt <= now:
                    return {"status": "ok", "branch": "default", "message": _("Dentro da janela de horário configurada.")}
                return {
                    "status": "wait",
                    "wait_spec": {
                        "wait_type": "time_window",
                        "wait_until": next_dt,
                        "next_run_at": next_dt,
                        "timeout_at": False,
                        "wait_config": cfg,
                    },
                    "message": _("Aguardando janela de horário (%s).", next_dt),
                }

            if ntype == "wait_condition":
                rules = cfg.get("rules") or {"logic": "AND", "rules": []}
                if self.env["mf.automation.evaluator"].evaluate(record, rules, extra_context=extra_context):
                    return {"status": "ok", "branch": "default", "message": _("Condição de espera já satisfeita.")}
                return {
                    "status": "wait",
                    "wait_spec": {
                        "wait_type": "condition",
                        "wait_until": False,
                        "next_run_at": now + timedelta(minutes=2),
                        "timeout_at": timeout_at,
                        "wait_config": cfg,
                    },
                    "message": _("Aguardando condição ser satisfeita."),
                }

            wait_type_map = {
                "wait_reply": "reply",
                "wait_activity": "activity_done",
                "wait_stage": "stage_change",
            }
            return {
                "status": "wait",
                "wait_spec": {
                    "wait_type": wait_type_map.get(ntype, "reply"),
                    "wait_until": False,
                    "next_run_at": timeout_at or (now + timedelta(hours=24)),
                    "timeout_at": timeout_at,
                    "wait_config": cfg,
                },
                "message": _("Aguardando evento (%s).", ntype),
            }

        # Ações de CRM (Seção 6.1)
        if ntype == "create_record":
            target_model = cfg.get("target_model") or "crm.lead"
            if target_model not in ("crm.lead", "res.partner"):
                raise UserError(_("Modelo não suportado para criação: %s", target_model))
            name_val = self.render_template(cfg.get("record_name") or "{{contact_name}}", record, cfg, extra_context)
            vals = {"name": name_val or f"Registro {record.display_name}"}
            if target_model == "crm.lead":
                vals["type"] = cfg.get("lead_type") or "opportunity"
                if record._name == "res.partner":
                    vals.update({
                        "partner_id": record.id,
                        "email_from": record.email or False,
                        "phone": record.phone or False,
                    })
                elif record._name == "crm.lead" and record.partner_id:
                    vals["partner_id"] = record.partner_id.id
                if cfg.get("team_id"):
                    vals["team_id"] = int(cfg["team_id"])
            created = self.env[target_model].sudo().with_context(**engine_ctx).create(vals)
            return {"status": "ok", "branch": "default", "message": _("Registro criado (%s #%s).", target_model, created.id)}

        if ntype == "update_field":
            field_name = (cfg.get("field_name") or "").strip()
            allowed = ALLOWED_UPDATE_FIELDS.get(record._name, set())
            if field_name not in allowed or field_name not in record._fields:
                raise UserError(_("Campo '%s' não autorizado para atualização em %s.", field_name, record._name))
            raw_val = cfg.get("value")
            if isinstance(raw_val, str) and "{{" in raw_val:
                raw_val = self.render_template(raw_val, record, cfg, extra_context)
            rec_sudo.write({field_name: raw_val})
            return {"status": "ok", "branch": "default", "message": _("Campo '%s' atualizado.", field_name)}

        if ntype in ("add_tag", "remove_tag"):
            tag_name = (cfg.get("tag_name") or "").strip()
            tag_id = int(cfg.get("tag_id") or 0)
            if record._name == "crm.lead":
                Tag = self.env["crm.tag"].sudo()
                field_tag = "tag_ids"
            else:
                Tag = self.env["res.partner.category"].sudo()
                field_tag = "category_id"
            tag = Tag.browse(tag_id).exists() if tag_id else (
                Tag.search([("name", "=", tag_name)], limit=1) or (Tag.create({"name": tag_name}) if (tag_name and ntype == "add_tag") else Tag)
            )
            if tag:
                op = 4 if ntype == "add_tag" else 3
                rec_sudo.write({field_tag: [(op, tag.id)]})
            return {"status": "ok", "branch": "default", "message": _("Tag atualizada (%s).", tag.name if tag else tag_name)}

        if ntype == "assign_owner":
            user_id = int(cfg.get("user_id") or 0)
            if not user_id:
                raise UserError(_("Selecione o responsável para atribuição."))
            rec_sudo.write({"user_id": user_id})
            return {"status": "ok", "branch": "default", "message": _("Responsável alterado para usuário #%s.", user_id)}

        if ntype == "assign_team":
            if "team_id" not in record._fields:
                raise UserError(_("Modelo %s não possui equipe comercial.", record._name))
            team_id = int(cfg.get("team_id") or 0)
            if not team_id:
                raise UserError(_("Selecione a equipe/pipeline."))
            rec_sudo.write({"team_id": team_id})
            return {"status": "ok", "branch": "default", "message": _("Equipe alterada para #%s.", team_id)}

        if ntype == "move_stage":
            if record._name != "crm.lead":
                raise UserError(_("Movimentação de etapa se aplica somente a Leads/Oportunidades."))
            stage_id = int(cfg.get("target_stage_id") or 0)
            pipe_id = int(cfg.get("target_pipeline_id") or 0)
            stage = self.env["crm.stage"].sudo().browse(stage_id).exists()
            if not stage:
                raise UserError(_("Etapa de destino #%s não encontrada.", stage_id))
            if pipe_id and "team_ids" in stage._fields and stage.team_ids and pipe_id not in stage.team_ids.ids:
                raise UserError(_("A etapa '%s' não pertence ao pipeline selecionado.", stage.name))
            vals = {"stage_id": stage.id}
            if pipe_id:
                vals["team_id"] = pipe_id
            if cfg.get("owner_id"):
                vals["user_id"] = int(cfg["owner_id"])
            rec_sudo.write(vals)
            return {"status": "ok", "branch": "default", "message": _("Oportunidade movida para etapa '%s'.", stage.name)}

        if ntype == "update_opportunity_metrics":
            if record._name != "crm.lead":
                raise UserError(_("Atualização de métricas comerciais aplica-se apenas a Leads/Oportunidades."))
            vals = {}
            if cfg.get("expected_revenue") not in (None, ""):
                vals["expected_revenue"] = float(cfg["expected_revenue"])
            if cfg.get("priority") in ("0", "1", "2", "3"):
                vals["priority"] = cfg["priority"]
            if cfg.get("probability") not in (None, ""):
                vals["probability"] = float(cfg["probability"])
            if vals:
                rec_sudo.write(vals)
            return {"status": "ok", "branch": "default", "message": _("Métricas da oportunidade atualizadas.")}

        if ntype == "mark_won":
            if not cfg.get("authorized"):
                raise UserError(_("Ação 'Marcar como Ganha' não autorizada na configuração do bloco."))
            if record._name == "crm.lead":
                rec_sudo.action_set_won()
            return {"status": "ok", "branch": "default", "message": _("Oportunidade marcada como Ganha.")}

        if ntype == "mark_lost":
            if not cfg.get("authorized"):
                raise UserError(_("Ação 'Marcar como Perdida' não autorizada na configuração do bloco."))
            if record._name == "crm.lead":
                rec_sudo.action_set_lost()
            return {"status": "ok", "branch": "default", "message": _("Oportunidade marcada como Perdida.")}

        if ntype in ("create_activity", "create_task"):
            summary = self.render_template(cfg.get("summary") or _("Acompanhamento automático"), record, cfg, extra_context)
            note = self.render_template(cfg.get("note") or "", record, cfg, extra_context)
            due_days = int(cfg.get("due_days") or 1)
            user_id = int(cfg.get("user_id") or 0) or (
                record.user_id.id if ("user_id" in record._fields and record.user_id) else self.env.user.id
            )
            if ntype == "create_task" and "project.task" in self.env:
                task_vals = {
                    "name": summary,
                    "description": note or summary,
                    "user_ids": [(4, user_id)],
                    "date_deadline": fields.Date.today() + timedelta(days=due_days),
                }
                if "partner_id" in record._fields and record.partner_id:
                    task_vals["partner_id"] = record.partner_id.id
                elif record._name == "res.partner":
                    task_vals["partner_id"] = record.id
                task = self.env["project.task"].sudo().create(task_vals)
                return {"status": "ok", "branch": "default", "message": _("Tarefa criada (#%s): %s.", task.id, summary)}

            deadline = fields.Date.today() + timedelta(days=due_days)
            act_summary = f"[Tarefa] {summary}" if ntype == "create_task" else summary
            act = rec_sudo.activity_schedule(
                "mail.mail_activity_data_todo",
                summary=act_summary,
                note=note or act_summary,
                user_id=user_id,
                date_deadline=deadline,
            )
            return {"status": "ok", "branch": "default", "message": _("Atividade criada (#%s): %s.", act.id, act_summary)}

        if ntype == "log_note":
            body = self.render_template(cfg.get("body") or cfg.get("note") or _("Nota automática"), record, cfg, extra_context)
            rec_sudo.message_post(body=body, message_type="comment", subtype_xmlid="mail.mt_note")
            return {"status": "ok", "branch": "default", "message": _("Nota registrada no histórico.")}

        if ntype == "link_partner":
            if record._name != "crm.lead":
                raise UserError(_("Associação de contato se aplica a Leads/Oportunidades."))
            mode = cfg.get("mode") or "link"
            if mode == "unlink":
                rec_sudo.write({"partner_id": False})
                return {"status": "ok", "branch": "default", "message": _("Contato desassociado do lead.")}
            partner_id = int(cfg.get("partner_id") or 0)
            if not partner_id:
                raise UserError(_("Selecione o contato para associar."))
            rec_sudo.write({"partner_id": partner_id})
            return {"status": "ok", "branch": "default", "message": _("Contato #%s associado ao lead.", partner_id)}

        # Comunicação multicanal (Seção 6.2)
        if ntype == "send_email":
            subj = self.render_template(cfg.get("subject") or "", record, cfg, extra_context)
            body = self.render_template(cfg.get("body") or "", record, cfg, extra_context)
            info = self._adapter_send_email(record, cfg, subj, body, idempotency_key)
            return {"status": "ok", "branch": "default", "message": _("E-mail processado."), "payload": info}

        if ntype == "send_whatsapp":
            body = self.render_template(cfg.get("body") or "", record, cfg, extra_context)
            if not body:
                raise UserError(_("Mensagem de WhatsApp vazia."))
            info = self._adapter_send_whatsapp(record, cfg, body, idempotency_key)
            return {"status": "ok", "branch": "default", "message": _("WhatsApp enviado."), "payload": info}

        if ntype == "call_webhook":
            info = self._adapter_call_webhook(record, cfg, idempotency_key, extra_context=extra_context)
            return {"status": "ok", "branch": "default", "message": _("Webhook chamado (HTTP %s).", info.get("status_code")), "payload": info}

        if ntype == "start_automation":
            target_id = int(cfg.get("target_automation_id") or 0)
            target_auto = self.env["mf.automation"].sudo().browse(target_id).exists()
            if not target_auto:
                raise UserError(_("Automação de destino #%s não encontrada.", target_id))
            if execution.depth >= 4:
                raise UserError(_("Profundidade máxima de encadeamento entre automações atingida (anti-loop)."))
            sub_exec = self.env["mf.automation.execution"].sudo().start_execution(
                target_auto,
                record,
                event_key=f"chain:{execution.id}:{target_auto.id}:{record.id}",
                payload=extra_context.get("payload") if extra_context else None,
                depth=execution.depth + 1,
            )
            return {
                "status": "ok",
                "branch": "default",
                "message": _("Automação '%s' iniciada (execução %s).", target_auto.name, sub_exec.name if sub_exec else "bloqueada"),
            }

        raise UserError(_("Tipo de bloco não suportado: %s", ntype))
