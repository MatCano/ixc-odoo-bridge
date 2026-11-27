"""Motor de execução persistente, fila assíncrona, esperas, retentativas e monitoramento."""
import json
import logging
import time
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .automation_action import AutomationTransientError

_logger = logging.getLogger(__name__)

MAX_STEPS_PER_CYCLE = 30
MAX_CHAIN_DEPTH = 5


class AutomationStepLog(models.Model):
    _name = "mf.automation.step.log"
    _description = "Histórico Cronológico de Execução de Automação"
    _order = "id asc"

    execution_id = fields.Many2one("mf.automation.execution", string="Execução", required=True, ondelete="cascade", index=True)
    automation_id = fields.Many2one(related="execution_id.automation_id", store=True, index=True)
    node_key = fields.Char("Chave do Bloco")
    node_name = fields.Char("Bloco")
    node_type = fields.Char("Tipo")
    status = fields.Selection(
        [
            ("ok", "Concluído"),
            ("wait", "Em Espera"),
            ("retry", "Agendada Retentativa"),
            ("error", "Erro"),
            ("cancelled", "Cancelado"),
        ],
        string="Status",
        required=True,
    )
    branch_taken = fields.Char("Ramo Seguido")
    idempotency_key = fields.Char("Chave de Idempotência", index=True)
    attempt = fields.Integer("Tentativa", default=1)
    message = fields.Text("Detalhes")
    sanitized_payload = fields.Text("Dados Sanitizados")
    executed_at = fields.Datetime("Executado em", default=fields.Datetime.now, required=True)
    duration_ms = fields.Integer("Duração (ms)", default=0)


class AutomationExecution(models.Model):
    _name = "mf.automation.execution"
    _description = "Instância de Execução de Automação CRM"
    _order = "create_date desc, id desc"

    name = fields.Char("Código", required=True, copy=False, readonly=True, default="Nova Execução")
    automation_id = fields.Many2one("mf.automation", string="Automação", required=True, ondelete="cascade", index=True)
    version_id = fields.Many2one("mf.automation.version", string="Versão Executada", readonly=True, ondelete="restrict")
    model_name = fields.Selection(
        [("crm.lead", "Leads e Oportunidades"), ("res.partner", "Contatos")],
        string="Modelo",
        required=True,
        index=True,
    )
    res_id = fields.Integer("ID do Registro", required=True, index=True)
    record_name = fields.Char("Registro", compute="_compute_record_name")

    state = fields.Selection(
        [
            ("queued", "Na Fila"),
            ("running", "Em Andamento"),
            ("waiting", "Aguardando"),
            ("done", "Concluída"),
            ("failed", "Falhou"),
            ("cancelled", "Cancelada"),
        ],
        string="Status",
        default="queued",
        required=True,
        index=True,
    )
    current_node_key = fields.Char("Bloco Atual")
    current_node_name = fields.Char("Nome do Bloco Atual", compute="_compute_current_node_name")

    wait_type = fields.Selection(
        [
            ("none", "Sem Espera"),
            ("duration", "Duração"),
            ("until_date", "Data Específica"),
            ("time_window", "Janela de Horário"),
            ("condition", "Condição"),
            ("reply", "Resposta / Evento"),
            ("activity_done", "Conclusão de Atividade"),
            ("stage_change", "Mudança de Etapa"),
        ],
        string="Tipo de Espera",
        default="none",
    )
    wait_until = fields.Datetime("Aguardar Até")
    timeout_at = fields.Datetime("Timeout Previsto")
    next_run_at = fields.Datetime("Próxima Ação Prevista", default=fields.Datetime.now, index=True)
    wait_config_json = fields.Text("Configuração de Espera (JSON)")
    payload_json = fields.Text("Contexto Sanitizado (JSON)")

    event_key = fields.Char("Chave do Evento (Deduplicação)", index=True, copy=False)
    depth = fields.Integer("Profundidade de Encadeamento", default=0)
    retry_count = fields.Integer("Tentativas", default=0)
    max_retries = fields.Integer("Limite de Tentativas", default=3)
    last_error = fields.Text("Último Erro")
    exit_reason = fields.Char("Motivo de Encerramento")

    started_at = fields.Datetime("Início", default=fields.Datetime.now)
    finished_at = fields.Datetime("Término")
    locked_at = fields.Datetime("Em processamento desde")

    step_log_ids = fields.One2many("mf.automation.step.log", "execution_id", string="Histórico de Ações")

    _event_key_uniq = models.Constraint(
        "unique(automation_id, event_key)",
        "Evento já disparado para esta automação (deduplicação).",
    )

    @api.model_create_multi
    def create(self, vals_list):
        seq = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == "Nova Execução":
                vals["name"] = seq.next_by_code("mf.automation.execution") or "AUT/NEW"
        return super().create(vals_list)

    def _compute_record_name(self):
        for rec in self:
            target = self.env[rec.model_name].sudo().browse(rec.res_id).exists() if rec.model_name and rec.res_id else False
            rec.record_name = target.display_name if target else f"{rec.model_name}#{rec.res_id}"

    @api.depends("current_node_key", "version_id.graph_json")
    def _compute_current_node_name(self):
        for rec in self:
            if not rec.current_node_key or not rec.version_id:
                rec.current_node_name = rec.current_node_key or ""
                continue
            graph = rec.version_id.get_graph()
            match = next((n.get("name") for n in graph.get("nodes", []) if n.get("node_key") == rec.current_node_key), None)
            rec.current_node_name = match or rec.current_node_key

    def _get_target_record(self):
        self.ensure_one()
        if not self.model_name or not self.res_id:
            return self.env["crm.lead"].browse()
        return self.env[self.model_name].sudo().browse(self.res_id).exists()

    def _get_graph_lookup(self):
        self.ensure_one()
        if self.version_id:
            graph = self.version_id.get_graph()
            trigger_cfg = self.version_id.get_trigger_config()
        else:
            auto = self.automation_id
            id_to_key = {n.id: n.node_key for n in auto.node_ids}
            graph = {
                "nodes": [
                    {
                        "node_key": n.node_key,
                        "name": n.name,
                        "category": n.category,
                        "node_type": n.node_type,
                        "config": n.get_config(),
                    }
                    for n in auto.node_ids
                ],
                "edges": [
                    {
                        "source_key": id_to_key.get(e.source_node_id.id),
                        "target_key": id_to_key.get(e.target_node_id.id),
                        "branch": e.branch,
                    }
                    for e in auto.edge_ids
                ],
            }
            trigger_cfg = auto._export_trigger_policy_dict()

        nodes_by_key = {n["node_key"]: n for n in graph.get("nodes", [])}
        edges_by_src = {}
        for e in graph.get("edges", []):
            edges_by_src.setdefault(e.get("source_key"), []).append(e)
        return nodes_by_key, edges_by_src, trigger_cfg

    @api.model
    def _is_eligible_for_entry(self, automation, record, trigger_cfg, event_key="", extra_context=None):
        if automation.state != "active":
            return False, "Automação não está ativa."
        if event_key and self.sudo().search_count([("automation_id", "=", automation.id), ("event_key", "=", event_key)]):
            return False, "Evento já processado anteriormente."

        active_states = ("queued", "running", "waiting")
        if not trigger_cfg.get("allow_concurrent"):
            if self.sudo().search_count([
                ("automation_id", "=", automation.id),
                ("model_name", "=", record._name),
                ("res_id", "=", record.id),
                ("state", "in", active_states),
            ]):
                return False, "Registro já possui execução ativa nesta automação."

        policy = trigger_cfg.get("reentry_policy") or "after_completion"
        past_execs = self.sudo().search(
            [
                ("automation_id", "=", automation.id),
                ("model_name", "=", record._name),
                ("res_id", "=", record.id),
            ],
            order="id desc",
            limit=5,
        )
        if past_execs:
            last_exec = past_execs[0]
            if not trigger_cfg.get("allow_reentry_after_exit") and last_exec.exit_reason:
                return False, "Reentrada após saída por condição está bloqueada."
            if policy == "never":
                return False, "Política bloqueia reentrada."
            if policy == "after_completion" and any(e.state in active_states for e in past_execs):
                return False, "Aguardando conclusão da execução anterior."
            if policy == "after_interval":
                hours = int(trigger_cfg.get("reentry_interval_hours") or 24)
                cutoff = fields.Datetime.now() - timedelta(hours=hours)
                if last_exec.started_at and last_exec.started_at > cutoff:
                    return False, "Intervalo mínimo de reentrada ainda não atingido."

        evaluator = self.env["mf.automation.evaluator"]
        entry_filter = trigger_cfg.get("entry_filter_json") or '{"logic": "AND", "rules": []}'
        if not evaluator.evaluate(record, entry_filter, extra_context=extra_context):
            return False, "Registro não atende ao filtro de entrada."
        return True, ""

    @api.model
    def start_execution(self, automation, record, event_key="", payload=None, depth=0, immediate=True):
        if depth > MAX_CHAIN_DEPTH:
            _logger.warning("Automação %s bloqueada por profundidade máxima (%s)", automation.id, depth)
            return self.browse()

        version = automation.published_version_id
        if not version:
            return self.browse()

        trigger_cfg = version.get_trigger_config()
        extra_ctx = {"payload": payload or {}}
        eligible, reason = self._is_eligible_for_entry(
            automation, record, trigger_cfg, event_key=event_key, extra_context=extra_ctx
        )
        if not eligible:
            _logger.debug("Automação %s não iniciada para %s#%s: %s", automation.id, record._name, record.id, reason)
            return self.browse()

        graph = version.get_graph()
        trigger_node = next((n for n in graph.get("nodes", []) if n.get("category") == "trigger"), None)
        if not trigger_node:
            return self.browse()

        runner = self.env["mf.automation.action.runner"]
        sanitized_payload = runner.sanitize_for_log(payload or {})
        execution = self.sudo().create({
            "automation_id": automation.id,
            "version_id": version.id,
            "model_name": record._name,
            "res_id": record.id,
            "state": "queued",
            "current_node_key": trigger_node["node_key"],
            "event_key": event_key or False,
            "depth": depth,
            "payload_json": json.dumps(sanitized_payload),
            "next_run_at": fields.Datetime.now(),
        })
        if immediate:
            execution.advance_execution()
        return execution

    def _find_next_node_key(self, edges_by_src, src_key, branch="default"):
        edges = edges_by_src.get(src_key, [])
        exact = [e["target_key"] for e in edges if e.get("branch") == branch]
        if exact:
            return exact[0]
        if branch != "default":
            fallback = [e["target_key"] for e in edges if e.get("branch") == "default"]
            if fallback:
                return fallback[0]
        return False

    def _check_stay_and_exit(self, record, trigger_cfg, extra_context=None):
        evaluator = self.env["mf.automation.evaluator"]
        stay_rules = trigger_cfg.get("stay_filter_json")
        if stay_rules and not evaluator.evaluate(record, stay_rules, extra_context=extra_context):
            return False, _("Condição de permanência deixou de ser verdadeira.")
        if trigger_cfg.get("exit_event") == "condition_met":
            exit_rules = trigger_cfg.get("exit_filter_json")
            if exit_rules:
                parsed = evaluator.parse_rules(exit_rules)
                if parsed.get("rules") and evaluator.evaluate(record, parsed, extra_context=extra_context):
                    return False, _("Condição de saída atendida.")
        return True, ""

    def _log_step(self, node_dict, status, branch="", message="", duration_ms=0, payload=None, attempt=1):
        self.ensure_one()
        runner = self.env["mf.automation.action.runner"]
        sanitized = runner.sanitize_for_log(payload or {})
        self.env["mf.automation.step.log"].sudo().create({
            "execution_id": self.id,
            "node_key": node_dict.get("node_key") or "",
            "node_name": node_dict.get("name") or "",
            "node_type": node_dict.get("node_type") or "",
            "status": status,
            "branch_taken": branch or "",
            "idempotency_key": f"mf_auto_{self.id}_{node_dict.get('node_key')}_v{self.version_id.version_number if self.version_id else 0}",
            "attempt": attempt,
            "message": runner.sanitize_text(message or ""),
            "sanitized_payload": json.dumps(sanitized) if sanitized else False,
            "duration_ms": duration_ms,
        })

    def advance_execution(self):
        runner = self.env["mf.automation.action.runner"]
        for execution in self.sudo():
            if execution.state in ("done", "failed", "cancelled"):
                continue

            record = execution._get_target_record()
            if not record:
                execution.write({
                    "state": "cancelled",
                    "exit_reason": _("Registro vinculado foi removido."),
                    "finished_at": fields.Datetime.now(),
                    "locked_at": False,
                })
                continue

            nodes_by_key, edges_by_src, trigger_cfg = execution._get_graph_lookup()
            raw_payload = json.loads(execution.payload_json or "{}")
            extra_ctx = {"payload": raw_payload}

            execution.write({"state": "running", "locked_at": fields.Datetime.now(), "wait_type": "none"})
            steps = 0
            while steps < MAX_STEPS_PER_CYCLE:
                steps += 1
                should_stay, reason = execution._check_stay_and_exit(record, trigger_cfg, extra_context=extra_ctx)
                if not should_stay:
                    execution.write({
                        "state": "cancelled",
                        "exit_reason": reason,
                        "finished_at": fields.Datetime.now(),
                        "locked_at": False,
                    })
                    break

                curr_key = execution.current_node_key
                node = nodes_by_key.get(curr_key)
                if not node:
                    execution.write({
                        "state": "done",
                        "finished_at": fields.Datetime.now(),
                        "locked_at": False,
                    })
                    break

                t0 = time.monotonic()
                try:
                    with self.env.cr.savepoint():
                        res = runner.execute_node(execution, node, record, extra_context=extra_ctx)
                except AutomationTransientError as err:
                    dur = int((time.monotonic() - t0) * 1000)
                    next_attempt = execution.retry_count + 1
                    if next_attempt <= (execution.max_retries or 3):
                        backoff_sec = min(3600, 30 * (2 ** (next_attempt - 1)))
                        next_run = fields.Datetime.now() + timedelta(seconds=backoff_sec)
                        execution._log_step(
                            node, "retry", message=str(err), duration_ms=dur, attempt=next_attempt
                        )
                        execution.write({
                            "state": "queued",
                            "retry_count": next_attempt,
                            "next_run_at": next_run,
                            "last_error": runner.sanitize_text(str(err))[:1000],
                            "locked_at": False,
                        })
                    else:
                        execution._log_step(
                            node, "error", message=str(err), duration_ms=dur, attempt=next_attempt
                        )
                        execution.write({
                            "state": "failed",
                            "retry_count": next_attempt,
                            "last_error": runner.sanitize_text(str(err))[:1000],
                            "finished_at": fields.Datetime.now(),
                            "locked_at": False,
                        })
                    break
                except Exception as err:  # noqa: BLE001 — isolamento de falha por execução
                    dur = int((time.monotonic() - t0) * 1000)
                    execution._log_step(
                        node, "error", message=str(err), duration_ms=dur, attempt=execution.retry_count + 1
                    )
                    execution.write({
                        "state": "failed",
                        "last_error": runner.sanitize_text(str(err))[:1000],
                        "finished_at": fields.Datetime.now(),
                        "locked_at": False,
                    })
                    break

                dur = int((time.monotonic() - t0) * 1000)
                status = res.get("status")
                if status == "wait":
                    spec = res.get("wait_spec") or {}
                    execution._log_step(node, "wait", message=res.get("message"), duration_ms=dur)
                    execution.write({
                        "state": "waiting",
                        "wait_type": spec.get("wait_type") or "duration",
                        "wait_until": spec.get("wait_until"),
                        "timeout_at": spec.get("timeout_at"),
                        "next_run_at": spec.get("next_run_at"),
                        "wait_config_json": json.dumps(spec.get("wait_config") or {}),
                        "retry_count": 0,
                        "last_error": False,
                        "locked_at": False,
                    })
                    break

                branch = res.get("branch") or "default"
                execution._log_step(
                    node, "ok", branch=branch, message=res.get("message"), duration_ms=dur, payload=res.get("payload")
                )

                if status == "end":
                    execution.write({
                        "state": "done",
                        "retry_count": 0,
                        "last_error": False,
                        "finished_at": fields.Datetime.now(),
                        "locked_at": False,
                    })
                    break

                nxt_key = execution._find_next_node_key(edges_by_src, curr_key, branch=branch)
                if not nxt_key:
                    execution.write({
                        "state": "done",
                        "current_node_key": False,
                        "retry_count": 0,
                        "last_error": False,
                        "finished_at": fields.Datetime.now(),
                        "locked_at": False,
                    })
                    break
                execution.write({"current_node_key": nxt_key, "retry_count": 0, "last_error": False})
            else:
                execution.write({
                    "state": "failed",
                    "last_error": _("Limite de blocos consecutivos sem espera atingido (proteção anti-loop)."),
                    "finished_at": fields.Datetime.now(),
                    "locked_at": False,
                })

    def _wake_to_branch(self, branch, message=""):
        self.ensure_one()
        nodes_by_key, edges_by_src, _cfg = self._get_graph_lookup()
        curr_node = nodes_by_key.get(self.current_node_key) or {"node_key": self.current_node_key, "name": "Espera", "node_type": "wait"}
        self._log_step(curr_node, "ok", branch=branch, message=message or _("Espera retomada (%s).", branch))
        nxt_key = self._find_next_node_key(edges_by_src, self.current_node_key, branch=branch)
        if not nxt_key:
            self.write({
                "state": "done",
                "wait_type": "none",
                "finished_at": fields.Datetime.now(),
                "locked_at": False,
            })
            return
        self.write({
            "state": "queued",
            "wait_type": "none",
            "wait_until": False,
            "timeout_at": False,
            "current_node_key": nxt_key,
            "next_run_at": fields.Datetime.now(),
        })
        self.advance_execution()

    @api.model
    def resume_waiting_on_event(self, model_name, res_id, event_type, extra_context=None):
        """Acorda esperas por evento/resposta OU encerra execuções por evento de saída."""
        active_execs = self.sudo().search([
            ("model_name", "=", model_name),
            ("res_id", "=", res_id),
            ("state", "in", ("queued", "waiting")),
        ])
        for ex in active_execs:
            _nodes, _edges, trigger_cfg = ex._get_graph_lookup()
            exit_ev = trigger_cfg.get("exit_event") or "none"
            should_exit = (
                (exit_ev == "won" and event_type == "won")
                or (exit_ev == "lost" and event_type == "lost")
                or (exit_ev == "won_or_lost" and event_type in ("won", "lost"))
                or (exit_ev == "stage_changed" and event_type == "stage_changed")
                or (exit_ev == "reply_received" and event_type == "reply_received")
            )
            if should_exit and trigger_cfg.get("cancel_pending_on_exit", True):
                ex.action_cancel_execution(reason=_("Encerrada pelo evento de saída: %s.", event_type))
                continue

            if ex.state != "waiting":
                continue

            if ex.wait_type == "reply" and event_type == "reply_received":
                ex._wake_to_branch("reply", _("Resposta do cliente recebida."))
            elif ex.wait_type == "activity_done" and event_type == "activity_done":
                ex._wake_to_branch("default", _("Atividade concluída."))
            elif ex.wait_type == "stage_change" and event_type == "stage_changed":
                wait_cfg = json.loads(ex.wait_config_json or "{}")
                target_stage_id = int(wait_cfg.get("target_stage_id") or 0)
                rec = ex._get_target_record()
                if not target_stage_id or (rec and "stage_id" in rec._fields and rec.stage_id.id == target_stage_id):
                    ex._wake_to_branch("default", _("Mudança de etapa detectada."))

    def action_cancel_execution(self, reason=""):
        for rec in self:
            if rec.state in ("done", "cancelled"):
                continue
            rec.sudo().write({
                "state": "cancelled",
                "wait_type": "none",
                "exit_reason": reason or _("Cancelada por %s.", self.env.user.name),
                "finished_at": fields.Datetime.now(),
                "locked_at": False,
            })
        return True

    def action_retry_execution(self):
        if not self.env.user.has_group("maisfibra_automacao.group_automacao_admin"):
            raise UserError(_("Apenas Administradores de Automações podem reprocessar falhas."))
        for rec in self:
            if rec.state not in ("failed", "queued", "waiting"):
                raise UserError(_("Apenas execuções com falha ou pendentes podem ser reprocessadas."))
            rec.sudo().write({
                "state": "queued",
                "retry_count": 0,
                "last_error": False,
                "next_run_at": fields.Datetime.now(),
                "locked_at": False,
            })
            rec.advance_execution()
        return True

    @api.model
    def _cron_process_queue(self, limit=100):
        now = fields.Datetime.now()
        # 1. Recupera execuções presas em 'running' há mais de 15 minutos
        stuck_cutoff = now - timedelta(minutes=15)
        stuck = self.sudo().search([("state", "=", "running"), ("locked_at", "<=", stuck_cutoff)], limit=limit)
        for ex in stuck:
            ex.write({"state": "queued", "next_run_at": now, "locked_at": False, "last_error": _("Execução travada recuperada pelo monitor.")})

        # 2. Execuções prontas na fila
        queued = self.sudo().search([
            ("state", "=", "queued"),
            "|", ("next_run_at", "=", False), ("next_run_at", "<=", now),
        ], order="id asc", limit=limit)
        for ex in queued:
            ex.advance_execution()

        # 3. Esperas vencidas, condições e timeouts
        waiting = self.sudo().search([("state", "=", "waiting")], order="id asc", limit=limit)
        evaluator = self.env["mf.automation.evaluator"]
        for ex in waiting:
            rec = ex._get_target_record()
            if not rec:
                ex.action_cancel_execution(_("Registro removido."))
                continue
            if ex.timeout_at and ex.timeout_at <= now:
                ex._wake_to_branch("timeout", _("Tempo limite de espera atingido (timeout)."))
                continue
            if ex.wait_type in ("duration", "until_date", "time_window") and ex.wait_until and ex.wait_until <= now:
                ex._wake_to_branch("default", _("Espera programada concluída."))
                continue
            if ex.wait_type == "condition":
                wait_cfg = json.loads(ex.wait_config_json or "{}")
                if evaluator.evaluate(rec, wait_cfg.get("rules") or {"logic": "AND", "rules": []}):
                    ex._wake_to_branch("default", _("Condição de espera satisfeita."))

    @api.model
    def _cron_evaluate_scheduled_triggers(self, limit=200):
        """Avalia gatilhos temporais: inatividade, datas específicas/relativas, atividades vencidas e entrada em segmento."""
        now = fields.Datetime.now()
        today = fields.Date.today()
        active_autos = self.env["mf.automation"].sudo().search([
            ("state", "=", "active"),
            ("trigger_type", "in", ("inactivity", "date_trigger", "activity_event", "segment_entered")),
            ("published_version_id", "!=", False),
        ])
        evaluator = self.env["mf.automation.evaluator"]
        for auto in active_autos:
            if auto.trigger_type == "inactivity":
                cutoff = now - timedelta(days=max(1, int(auto.trigger_inactivity_days or 3)))
                domain = [("write_date", "<=", cutoff)]
                if auto.model_name == "crm.lead":
                    domain.append(("active", "=", True))
                recs = self.env[auto.model_name].sudo().search(domain, limit=limit)
                for rec in recs:
                    last_dt = evaluator._resolve_field_value(rec, "last_activity_date")
                    if not last_dt or last_dt <= cutoff:
                        self.start_execution(auto, rec, event_key=f"inact:{auto.id}:{rec.id}:{today}")

            elif auto.trigger_type == "date_trigger":
                if auto.trigger_date_mode == "fixed":
                    if auto.trigger_date_fixed and auto.trigger_date_fixed <= now:
                        recs = self.env[auto.model_name].sudo().search([], limit=limit)
                        for rec in recs:
                            self.start_execution(auto, rec, event_key=f"fixed_dt:{auto.id}:{rec.id}:{auto.trigger_date_fixed.date()}")
                else:
                    field_name = (auto.trigger_date_field or "").strip()
                    if field_name and field_name in self.env[auto.model_name]._fields:
                        target_date = today - timedelta(days=int(auto.trigger_date_offset_days or 0))
                        recs = self.env[auto.model_name].sudo().search([(field_name, "=", target_date)], limit=limit)
                        for rec in recs:
                            self.start_execution(auto, rec, event_key=f"rel_dt:{auto.id}:{rec.id}:{target_date}")

            elif auto.trigger_type == "activity_event" and auto.trigger_activity_state == "overdue":
                acts = self.env["mail.activity"].sudo().search([
                    ("res_model", "=", auto.model_name),
                    ("date_deadline", "<", today),
                ], limit=limit)
                for act in acts:
                    rec = self.env[auto.model_name].sudo().browse(act.res_id).exists()
                    if rec:
                        self.start_execution(auto, rec, event_key=f"act_overdue:{auto.id}:{act.id}:{today}")

            elif auto.trigger_type == "segment_entered" and auto.trigger_segment_id:
                recs = self.env[auto.model_name].sudo().search([], limit=limit)
                for rec in recs:
                    if auto.trigger_segment_id.evaluate_record(rec):
                        self.start_execution(auto, rec, event_key=f"seg:{auto.id}:{auto.trigger_segment_id.id}:{rec.id}")

    @api.model
    def get_monitor_data(self, state_filter="all"):
        execs = self.sudo().search([] if state_filter in ("all", "") else [("state", "=", state_filter)], order="id desc", limit=100)
        all_execs = self.sudo().search([])
        kpis = {
            "total": len(all_execs),
            "queued": len(all_execs.filtered(lambda e: e.state == "queued")),
            "running": len(all_execs.filtered(lambda e: e.state == "running")),
            "waiting": len(all_execs.filtered(lambda e: e.state == "waiting")),
            "done": len(all_execs.filtered(lambda e: e.state == "done")),
            "failed": len(all_execs.filtered(lambda e: e.state == "failed")),
            "cancelled": len(all_execs.filtered(lambda e: e.state == "cancelled")),
        }
        return {
            "kpis": kpis,
            "executions": [
                {
                    "id": e.id,
                    "name": e.name,
                    "automation_id": e.automation_id.id,
                    "automation_name": e.automation_id.name,
                    "version": e.version_id.version_number if e.version_id else 0,
                    "model_name": e.model_name,
                    "res_id": e.res_id,
                    "record_name": e.record_name,
                    "state": e.state,
                    "current_node_name": e.current_node_name,
                    "wait_type": e.wait_type,
                    "next_run_at": e.next_run_at.isoformat() if e.next_run_at else False,
                    "started_at": e.started_at.isoformat() if e.started_at else False,
                    "finished_at": e.finished_at.isoformat() if e.finished_at else False,
                    "retry_count": e.retry_count,
                    "last_error": e.last_error or "",
                    "exit_reason": e.exit_reason or "",
                }
                for e in execs
            ],
        }
