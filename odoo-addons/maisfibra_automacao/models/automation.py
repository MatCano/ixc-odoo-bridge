"""Modelos de definição, blocos visuais, validação e versionamento imutável de Automações."""
import json
import re
import uuid
from collections import deque

from odoo import _, api, fields, models
from odoo.exceptions import UserError

TRIGGER_TYPES = [
    ("partner_created", "Novo contato criado"),
    ("lead_created", "Novo lead ou oportunidade criado"),
    ("lead_assigned", "Lead atribuído a um responsável"),
    ("owner_changed", "Alteração de responsável"),
    ("stage_changed", "Alteração de etapa do pipeline"),
    ("stage_entered", "Entrada em uma etapa específica"),
    ("stage_exited", "Saída de uma etapa específica"),
    ("opportunity_status_changed", "Mudança de status da oportunidade"),
    ("field_changed", "Alteração de campo do contato/oportunidade"),
    ("activity_event", "Atividade criada, concluída ou vencida"),
    ("lead_won_or_lost", "Registro marcado como ganho ou perdido"),
    ("inactivity", "Registro sem interação por determinado período"),
    ("date_trigger", "Data específica ou relativa a um campo"),
    ("segment_entered", "Entrada em uma lista ou segmento"),
    ("webhook_received", "Evento / Webhook de integração externa"),
]

NODE_CATEGORIES = [
    ("trigger", "Gatilhos"),
    ("condition", "Condições e Segmentação"),
    ("crm_action", "Ações de CRM"),
    ("communication", "Comunicação"),
    ("wait", "Esperas e Agendamentos"),
    ("integration", "Integrações"),
    ("flow_control", "Controle de Fluxo"),
]

NODE_TYPES = [
    # Gatilhos
    ("trigger_entry", "Gatilho de Entrada"),
    # Condições e segmentação
    ("condition_if", "Condição (SIM / NÃO)"),
    ("segment_check", "Verificar Segmento"),
    # Ações de CRM
    ("create_record", "Criar Contato / Lead"),
    ("update_field", "Atualizar Campo"),
    ("add_tag", "Adicionar Tag"),
    ("remove_tag", "Remover Tag"),
    ("assign_owner", "Alterar Responsável"),
    ("assign_team", "Alterar Equipe / Pipeline"),
    ("move_stage", "Mover Etapa / Pipeline"),
    ("update_opportunity_metrics", "Atualizar Valor / Prioridade / Probabilidade"),
    ("mark_won", "Marcar Oportunidade como Ganha"),
    ("mark_lost", "Marcar Oportunidade como Perdida"),
    ("create_activity", "Criar Atividade"),
    ("create_task", "Criar Tarefa"),
    ("log_note", "Registrar Nota no Histórico"),
    ("link_partner", "Associar / Desassociar Contato"),
    # Comunicação
    ("send_email", "Enviar E-mail"),
    ("send_whatsapp", "Enviar Mensagem WhatsApp"),
    # Esperas e agendamentos
    ("wait_duration", "Aguardar Tempo (Min/Horas/Dias)"),
    ("wait_until_date", "Aguardar até Data / Horário"),
    ("wait_time_window", "Aguardar Janela de Horário / Dia"),
    ("wait_condition", "Aguardar Condição"),
    ("wait_reply", "Aguardar Resposta / Evento Externo"),
    ("wait_activity", "Aguardar Atividade Concluída"),
    ("wait_stage", "Aguardar Mudança de Etapa"),
    # Integrações
    ("call_webhook", "Enviar Webhook HTTP"),
    # Controle de fluxo
    ("start_automation", "Iniciar Outra Automação"),
    ("end_flow", "Encerrar Automação"),
]

ALLOWED_TEMPLATE_VARS = {
    "contact_name",
    "company_name",
    "owner_name",
    "lead_name",
    "expected_revenue",
    "stage_name",
    "phone",
    "email",
    "custom_link",
}

TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


class AutomationVersion(models.Model):
    _name = "mf.automation.version"
    _description = "Versão Publicada de Automação"
    _order = "version_number desc, id desc"

    automation_id = fields.Many2one("mf.automation", string="Automação", required=True, ondelete="cascade", index=True)
    version_number = fields.Integer("Versão", required=True)
    name = fields.Char("Identificação", compute="_compute_name", store=True)
    graph_json = fields.Text("Snapshot do Fluxo (JSON)", required=True)
    trigger_config_json = fields.Text("Snapshot do Gatilho e Políticas (JSON)", required=True)
    published_by = fields.Many2one("res.users", string="Publicado por", default=lambda self: self.env.user, readonly=True)
    published_at = fields.Datetime("Publicado em", default=fields.Datetime.now, readonly=True)

    @api.depends("automation_id.name", "version_number")
    def _compute_name(self):
        for rec in self:
            rec.name = f"{rec.automation_id.name or 'Automação'} · v{rec.version_number}"

    def get_graph(self):
        self.ensure_one()
        return json.loads(self.graph_json or '{"nodes": [], "edges": []}')

    def get_trigger_config(self):
        self.ensure_one()
        return json.loads(self.trigger_config_json or "{}")


class AutomationNode(models.Model):
    _name = "mf.automation.node"
    _description = "Bloco do Construtor de Automação"
    _order = "id asc"

    automation_id = fields.Many2one("mf.automation", string="Automação", required=True, ondelete="cascade", index=True)
    node_key = fields.Char("Chave do Bloco", required=True, default=lambda self: f"n_{uuid.uuid4().hex[:8]}")
    name = fields.Char("Título do Bloco", required=True)
    category = fields.Selection(NODE_CATEGORIES, string="Categoria", required=True, default="crm_action")
    node_type = fields.Selection(NODE_TYPES, string="Tipo de Bloco", required=True, default="log_note")
    pos_x = fields.Integer("Posição X", default=80)
    pos_y = fields.Integer("Posição Y", default=80)
    config_json = fields.Text("Configuração (JSON)", default="{}")

    _node_key_uniq = models.Constraint(
        "unique(automation_id, node_key)",
        "A chave do bloco deve ser única dentro da automação.",
    )

    def get_config(self):
        self.ensure_one()
        try:
            return json.loads(self.config_json or "{}")
        except ValueError:
            return {}


class AutomationEdge(models.Model):
    _name = "mf.automation.edge"
    _description = "Conexão entre Blocos de Automação"
    _order = "id asc"

    automation_id = fields.Many2one("mf.automation", string="Automação", required=True, ondelete="cascade", index=True)
    source_node_id = fields.Many2one("mf.automation.node", string="Bloco de Origem", required=True, ondelete="cascade")
    target_node_id = fields.Many2one("mf.automation.node", string="Bloco de Destino", required=True, ondelete="cascade")
    branch = fields.Selection(
        [
            ("default", "Próximo"),
            ("true", "SIM (Verdadeiro)"),
            ("false", "NÃO (Falso)"),
            ("timeout", "Timeout"),
            ("reply", "Resposta / Evento"),
        ],
        string="Ramo",
        default="default",
        required=True,
    )
    label = fields.Char("Rótulo")

    _no_self_loop = models.Constraint(
        "CHECK(source_node_id != target_node_id)",
        "Um bloco não pode se conectar diretamente a ele mesmo.",
    )
    _unique_edge_branch = models.Constraint(
        "unique(automation_id, source_node_id, target_node_id, branch)",
        "Conexão duplicada no mesmo ramo.",
    )


class Automation(models.Model):
    _name = "mf.automation"
    _description = "Fluxo de Automação de CRM"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "write_date desc, id desc"

    name = fields.Char("Nome da Automação", required=True, tracking=True, index=True)
    description = fields.Text("Descrição")
    category = fields.Char("Categoria", default="Comercial", tracking=True)
    state = fields.Selection(
        [
            ("draft", "Rascunho"),
            ("active", "Ativa"),
            ("paused", "Pausada"),
            ("archived", "Arquivada"),
        ],
        string="Status",
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    active = fields.Boolean("Ativo no Odoo", default=True)
    responsible_id = fields.Many2one(
        "res.users", string="Responsável", default=lambda self: self.env.user, tracking=True
    )

    model_name = fields.Selection(
        [("crm.lead", "Leads e Oportunidades"), ("res.partner", "Contatos")],
        string="Objeto Principal",
        default="crm.lead",
        required=True,
        tracking=True,
    )
    trigger_type = fields.Selection(
        TRIGGER_TYPES, string="Gatilho de Entrada", default="lead_created", required=True, tracking=True
    )
    trigger_field_name = fields.Char("Campo Monitorado")
    trigger_activity_state = fields.Selection(
        [("created", "Criada"), ("done", "Concluída"), ("overdue", "Vencida")],
        string="Evento da Atividade",
        default="created",
    )
    trigger_won_lost = fields.Selection(
        [("won", "Somente Ganho"), ("lost", "Somente Perdido"), ("both", "Ganho ou Perdido")],
        string="Status de Fechamento",
        default="both",
    )
    trigger_pipeline_id = fields.Many2one("crm.team", string="Pipeline / Equipe do Gatilho")
    trigger_stage_id = fields.Many2one("crm.stage", string="Etapa de Destino / Alvo")
    trigger_stage_from_id = fields.Many2one("crm.stage", string="Etapa de Origem (Saída)")
    trigger_segment_id = fields.Many2one("mf.automation.segment", string="Segmento do Gatilho")
    trigger_inactivity_days = fields.Integer("Dias sem Interação", default=3)
    trigger_date_mode = fields.Selection(
        [("fixed", "Data/Hora Específica"), ("relative_field", "Relativa a Campo de Data")],
        string="Tipo de Gatilho por Data",
        default="relative_field",
    )
    trigger_date_fixed = fields.Datetime("Data/Hora Específica")
    trigger_date_field = fields.Char("Campo de Data Referência", default="date_deadline")
    trigger_date_offset_days = fields.Integer("Deslocamento (dias)", default=0)
    webhook_token = fields.Char(
        "Token do Webhook", default=lambda self: uuid.uuid4().hex, copy=False, readonly=True, index=True
    )

    # Segmentação e políticas (Seções 5 e 9)
    entry_filter_json = fields.Text("Filtro de Entrada (JSON)", default='{"logic": "AND", "rules": []}')
    stay_filter_json = fields.Text("Condição de Permanência (JSON)", default='{"logic": "AND", "rules": []}')
    exit_event = fields.Selection(
        [
            ("none", "Nenhum"),
            ("won", "Oportunidade Ganha"),
            ("lost", "Oportunidade Perdida"),
            ("won_or_lost", "Ganha ou Perdida"),
            ("stage_changed", "Mudança de Etapa"),
            ("reply_received", "Resposta Recebida do Cliente"),
            ("condition_met", "Condição de Saída Atendida"),
        ],
        string="Evento de Saída Antecipada",
        default="won",
    )
    exit_filter_json = fields.Text("Condição de Saída (JSON)", default='{"logic": "AND", "rules": []}')

    allow_concurrent = fields.Boolean("Permitir Execuções Simultâneas no Mesmo Registro", default=False)
    reentry_policy = fields.Selection(
        [
            ("never", "Nunca reentrar"),
            ("always", "Sempre que o evento ocorrer"),
            ("after_completion", "Somente após concluir execução anterior"),
            ("after_interval", "Após intervalo mínimo (horas)"),
        ],
        string="Política de Reentrada",
        default="after_completion",
        required=True,
    )
    reentry_interval_hours = fields.Integer("Intervalo de Reentrada (horas)", default=24)
    allow_reentry_after_exit = fields.Boolean("Permitir Reinício após Saída por Condição", default=False)
    cancel_pending_on_exit = fields.Boolean("Cancelar Ações Pendentes na Saída", default=True)
    version_migration_policy = fields.Selection(
        [
            ("keep_original", "Manter execuções ativas na versão original"),
            ("migrate_safe", "Migrar execuções se o bloco atual continuar compatível"),
        ],
        string="Política de Versionamento",
        default="keep_original",
        required=True,
    )

    node_ids = fields.One2many("mf.automation.node", "automation_id", string="Blocos do Fluxo")
    edge_ids = fields.One2many("mf.automation.edge", "automation_id", string="Conexões")
    version_ids = fields.One2many("mf.automation.version", "automation_id", string="Versões Publicadas")
    published_version_id = fields.Many2one("mf.automation.version", string="Versão Publicada", readonly=True, copy=False)
    published_version_number = fields.Integer("Nº Versão Publicada", related="published_version_id.version_number", store=True)

    execution_ids = fields.One2many("mf.automation.execution", "automation_id", string="Execuções")
    execution_count = fields.Integer("Processados", compute="_compute_metrics")
    completed_count = fields.Integer("Concluídas", compute="_compute_metrics")
    failed_count = fields.Integer("Com Erro", compute="_compute_metrics")
    waiting_count = fields.Integer("Aguardando", compute="_compute_metrics")
    last_execution_at = fields.Datetime("Última Execução", compute="_compute_metrics")
    validation_errors_text = fields.Text("Diagnóstico de Validação", readonly=True)

    @api.depends("execution_ids.state", "execution_ids.started_at")
    def _compute_metrics(self):
        Exec = self.env["mf.automation.execution"].sudo()
        for rec in self:
            execs = Exec.search([("automation_id", "=", rec.id)])
            rec.execution_count = len(execs)
            rec.completed_count = len(execs.filtered(lambda e: e.state == "done"))
            rec.failed_count = len(execs.filtered(lambda e: e.state == "failed"))
            rec.waiting_count = len(execs.filtered(lambda e: e.state in ("queued", "running", "waiting")))
            rec.last_execution_at = max(execs.mapped("started_at"), default=False)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        Node = self.env["mf.automation.node"]
        for rec in records:
            if not rec.node_ids:
                Node.create({
                    "automation_id": rec.id,
                    "node_key": "trigger_1",
                    "name": dict(TRIGGER_TYPES).get(rec.trigger_type, "Gatilho"),
                    "category": "trigger",
                    "node_type": "trigger_entry",
                    "pos_x": 80,
                    "pos_y": 140,
                    "config_json": json.dumps({"trigger_type": rec.trigger_type}),
                })
        return records

    def _validate_template_variables(self, text):
        errors = []
        if not text:
            return errors
        for var in TEMPLATE_VAR_RE.findall(text):
            if var in ALLOWED_TEMPLATE_VARS:
                continue
            if var.startswith("field.") and len(var.split(".", 1)[1]) > 0:
                field_name = var.split(".", 1)[1]
                if field_name in self.env[self.model_name]._fields and "__" not in field_name:
                    continue
            if var.startswith("payload.") and "__" not in var:
                continue
            errors.append(_("Variável não reconhecida ou não permitida no template: {{%s}}.", var))
        return errors

    def action_validate_flow(self):
        self.ensure_one()
        evaluator = self.env["mf.automation.evaluator"]
        errors = []
        node_errors = {}

        def _add_node_err(key, msg):
            node_errors.setdefault(key, []).append(msg)
            errors.append(msg)

        for label, raw in (
            ("Filtro de Entrada", self.entry_filter_json),
            ("Condição de Permanência", self.stay_filter_json),
            ("Condição de Saída", self.exit_filter_json),
        ):
            for err in evaluator.validate_rules(self.model_name, raw):
                errors.append(f"{label}: {err}")

        if self.trigger_type == "segment_entered" and not self.trigger_segment_id:
            errors.append(_("Selecione um Segmento para o gatilho 'Entrada em uma lista ou segmento'."))
        if self.trigger_type in ("stage_entered", "stage_exited") and not self.trigger_stage_id:
            errors.append(_("Selecione a etapa alvo para o gatilho de etapa."))

        nodes = self.node_ids
        triggers = nodes.filtered(lambda n: n.category == "trigger")
        if len(triggers) != 1:
            errors.append(_("O fluxo deve possuir exatamente 1 bloco de Gatilho (encontrados: %s).", len(triggers)))

        if not nodes:
            errors.append(_("O fluxo não possui blocos."))

        adj = {n.id: [] for n in nodes}
        edges_by_source = {n.id: [] for n in nodes}
        for edge in self.edge_ids:
            if edge.source_node_id.id in adj and edge.target_node_id.id in adj:
                adj[edge.source_node_id.id].append(edge.target_node_id.id)
                edges_by_source[edge.source_node_id.id].append(edge)

        # Alcance BFS a partir do gatilho
        reachable = set()
        if triggers:
            queue = deque([triggers[0].id])
            reachable.add(triggers[0].id)
            while queue:
                curr = queue.popleft()
                for nxt in adj.get(curr, []):
                    if nxt not in reachable:
                        reachable.add(nxt)
                        queue.append(nxt)

        for node in nodes:
            cfg = node.get_config()
            if triggers and node.id not in reachable:
                _add_node_err(node.node_key, _("Bloco '%s' está desconectado do gatilho.", node.name))

            out_edges = edges_by_source.get(node.id, [])
            if node.category == "trigger" and not out_edges:
                _add_node_err(node.node_key, _("O gatilho '%s' precisa estar conectado a pelo menos um bloco.", node.name))

            if node.node_type == "condition_if":
                branches = {e.branch for e in out_edges}
                if "true" not in branches or "false" not in branches:
                    _add_node_err(node.node_key, _("Condição '%s' deve ter saídas configuradas para SIM e NÃO.", node.name))
                rules_raw = cfg.get("rules") or {"logic": "AND", "rules": []}
                for err in evaluator.validate_rules(self.model_name, rules_raw):
                    _add_node_err(node.node_key, f"{node.name}: {err}")

            elif node.node_type == "segment_check":
                branches = {e.branch for e in out_edges}
                if "true" not in branches and "false" not in branches:
                    _add_node_err(node.node_key, _("Verificação de segmento '%s' sem conexões SIM/NÃO.", node.name))
                if not cfg.get("segment_id"):
                    _add_node_err(node.node_key, _("Selecione o segmento em '%s'.", node.name))

            elif node.node_type == "move_stage":
                stage_id = int(cfg.get("target_stage_id") or 0)
                pipe_id = int(cfg.get("target_pipeline_id") or 0)
                if not stage_id:
                    _add_node_err(node.node_key, _("Bloco '%s': selecione a etapa de destino.", node.name))
                else:
                    stage = self.env["crm.stage"].sudo().browse(stage_id).exists()
                    if not stage:
                        _add_node_err(node.node_key, _("Bloco '%s': etapa de destino inexistente.", node.name))
                    elif pipe_id and "team_ids" in stage._fields and stage.team_ids and pipe_id not in stage.team_ids.ids:
                        _add_node_err(
                            node.node_key,
                            _("Bloco '%s': a etapa '%s' não pertence ao pipeline selecionado.", node.name, stage.name),
                        )

            elif node.node_type in ("mark_won", "mark_lost"):
                if not cfg.get("authorized"):
                    _add_node_err(
                        node.node_key,
                        _("Bloco '%s': requer marcação de autorização explícita para fechar oportunidade.", node.name),
                    )

            elif node.node_type in ("send_email", "send_whatsapp"):
                msg_text = cfg.get("body") or ""
                if not msg_text and not cfg.get("template_id"):
                    _add_node_err(node.node_key, _("Bloco '%s': informe mensagem ou template.", node.name))
                for err in self._validate_template_variables(msg_text):
                    _add_node_err(node.node_key, f"{node.name}: {err}")
                if cfg.get("subject"):
                    for err in self._validate_template_variables(cfg["subject"]):
                        _add_node_err(node.node_key, f"{node.name}: {err}")

            elif node.node_type == "call_webhook":
                url = (cfg.get("url") or "").strip()
                if not (url.startswith("https://") or url.startswith("http://")):
                    _add_node_err(node.node_key, _("Bloco '%s': informe URL HTTP(S) válida.", node.name))

            elif node.node_type == "start_automation":
                target_auto_id = int(cfg.get("target_automation_id") or 0)
                if not target_auto_id or target_auto_id == self.id:
                    _add_node_err(node.node_key, _("Bloco '%s': selecione outra automação válida (sem autorreferência).", node.name))

        # Detecta ciclos sem bloco de espera no caminho
        wait_ids = {n.id for n in nodes if n.category == "wait"}
        state_dfs = {}

        def _has_tight_cycle(nid):
            state_dfs[nid] = 1
            for nxt in adj.get(nid, []):
                if nxt in wait_ids:
                    continue
                if state_dfs.get(nxt) == 1:
                    return True
                if state_dfs.get(nxt, 0) == 0 and _has_tight_cycle(nxt):
                    return True
            state_dfs[nid] = 2
            return False

        for nid in reachable:
            if nid not in wait_ids and state_dfs.get(nid, 0) == 0 and _has_tight_cycle(nid):
                errors.append(_("Ciclo fechado detectado sem bloco de Espera intermediário."))
                break

        self.sudo().write({"validation_errors_text": "\n".join(errors) if errors else False})
        return {"valid": not errors, "errors": errors, "node_errors": node_errors}

    def action_publish(self):
        if not self.env.user.has_group("maisfibra_automacao.group_automacao_admin"):
            raise UserError(_("Apenas Administradores de Automações podem publicar fluxos."))
        for rec in self:
            res = rec.action_validate_flow()
            if not res["valid"]:
                raise UserError(_("Corrija os erros antes de publicar:\n• %s", "\n• ".join(res["errors"])))

            last_num = max(rec.version_ids.mapped("version_number") or [0])
            new_num = last_num + 1
            id_to_key = {n.id: n.node_key for n in rec.node_ids}
            graph_snapshot = {
                "nodes": [
                    {
                        "node_key": n.node_key,
                        "name": n.name,
                        "category": n.category,
                        "node_type": n.node_type,
                        "pos_x": n.pos_x,
                        "pos_y": n.pos_y,
                        "config": n.get_config(),
                    }
                    for n in rec.node_ids
                ],
                "edges": [
                    {
                        "source_key": id_to_key.get(e.source_node_id.id),
                        "target_key": id_to_key.get(e.target_node_id.id),
                        "branch": e.branch,
                        "label": e.label or "",
                    }
                    for e in rec.edge_ids
                ],
            }
            trigger_snapshot = rec._export_trigger_policy_dict()
            version = self.env["mf.automation.version"].sudo().create({
                "automation_id": rec.id,
                "version_number": new_num,
                "graph_json": json.dumps(graph_snapshot),
                "trigger_config_json": json.dumps(trigger_snapshot),
                "published_by": self.env.user.id,
            })
            rec.write({
                "state": "active",
                "active": True,
                "published_version_id": version.id,
            })
            rec.message_post(
                body=_("🚀 Versão <b>v%s</b> publicada e ativada por %s.", new_num, self.env.user.name),
                subtype_xmlid="mail.mt_note",
            )
        return True

    def _export_trigger_policy_dict(self):
        self.ensure_one()
        return {
            "model_name": self.model_name,
            "trigger_type": self.trigger_type,
            "trigger_field_name": self.trigger_field_name or "",
            "trigger_activity_state": self.trigger_activity_state,
            "trigger_won_lost": self.trigger_won_lost,
            "trigger_pipeline_id": self.trigger_pipeline_id.id or False,
            "trigger_stage_id": self.trigger_stage_id.id or False,
            "trigger_stage_from_id": self.trigger_stage_from_id.id or False,
            "trigger_segment_id": self.trigger_segment_id.id or False,
            "trigger_inactivity_days": self.trigger_inactivity_days,
            "trigger_date_mode": self.trigger_date_mode,
            "trigger_date_fixed": self.trigger_date_fixed.isoformat() if self.trigger_date_fixed else False,
            "trigger_date_field": self.trigger_date_field or "",
            "trigger_date_offset_days": self.trigger_date_offset_days,
            "entry_filter_json": self.entry_filter_json or '{"logic": "AND", "rules": []}',
            "stay_filter_json": self.stay_filter_json or '{"logic": "AND", "rules": []}',
            "exit_event": self.exit_event,
            "exit_filter_json": self.exit_filter_json or '{"logic": "AND", "rules": []}',
            "allow_concurrent": bool(self.allow_concurrent),
            "reentry_policy": self.reentry_policy,
            "reentry_interval_hours": self.reentry_interval_hours,
            "allow_reentry_after_exit": bool(self.allow_reentry_after_exit),
            "cancel_pending_on_exit": bool(self.cancel_pending_on_exit),
            "version_migration_policy": self.version_migration_policy,
        }

    def action_pause(self):
        if not self.env.user.has_group("maisfibra_automacao.group_automacao_admin"):
            raise UserError(_("Apenas Administradores de Automações podem pausar fluxos."))
        self.write({"state": "paused"})
        return True

    def action_archive_automation(self):
        if not self.env.user.has_group("maisfibra_automacao.group_automacao_admin"):
            raise UserError(_("Apenas Administradores de Automações podem arquivar fluxos."))
        self.write({"state": "archived"})
        return True

    def action_set_draft(self):
        self.write({"state": "draft"})
        return True

    def action_duplicate_automation(self):
        self.ensure_one()
        copy_auto = self.copy({
            "name": _("%s (Cópia)", self.name),
            "state": "draft",
            "published_version_id": False,
        })
        key_map = {}
        for node in self.node_ids:
            cloned_node = self.env["mf.automation.node"].create({
                "automation_id": copy_auto.id,
                "node_key": node.node_key,
                "name": node.name,
                "category": node.category,
                "node_type": node.node_type,
                "pos_x": node.pos_x,
                "pos_y": node.pos_y,
                "config_json": node.config_json,
            })
            key_map[node.id] = cloned_node.id
        for edge in self.edge_ids:
            if edge.source_node_id.id in key_map and edge.target_node_id.id in key_map:
                self.env["mf.automation.edge"].create({
                    "automation_id": copy_auto.id,
                    "source_node_id": key_map[edge.source_node_id.id],
                    "target_node_id": key_map[edge.target_node_id.id],
                    "branch": edge.branch,
                    "label": edge.label,
                })
        return {
            "type": "ir.actions.act_window",
            "res_model": "mf.automation",
            "res_id": copy_auto.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_builder(self):
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "name": _("Construtor Visual: %s", self.name),
            "tag": "maisfibra_automacao.builder",
            "params": {"automation_id": self.id},
            "context": {"active_id": self.id},
        }

    def action_open_executions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Execuções: %s", self.name),
            "res_model": "mf.automation.execution",
            "view_mode": "list,form",
            "domain": [("automation_id", "=", self.id)],
        }

    def get_flow_preview(self):
        self.ensure_one()
        triggers = self.node_ids.filtered(lambda n: n.category == "trigger")
        if not triggers:
            return []
        by_id = {n.id: n for n in self.node_ids}
        edges_by_src = {}
        for edge in self.edge_ids:
            edges_by_src.setdefault(edge.source_node_id.id, []).append(edge)

        paths = []

        def _dfs(node_id, path, visited):
            if len(paths) >= 25:
                return
            node = by_id.get(node_id)
            if not node:
                return
            out_edges = edges_by_src.get(node_id, [])
            if not out_edges:
                paths.append(path)
                return
            for edge in out_edges:
                target = by_id.get(edge.target_node_id.id)
                if not target:
                    continue
                step = f"[{edge.branch.upper()}] {target.name}"
                if edge.target_node_id.id in visited:
                    paths.append(path + [f"{step} (retorna ao bloco)"])
                else:
                    _dfs(edge.target_node_id.id, path + [step], visited | {edge.target_node_id.id})

        root = triggers[0]
        _dfs(root.id, [f"Gatilho: {root.name}"], {root.id})
        return [" → ".join(p) for p in paths]

    def get_builder_data(self):
        self.ensure_one()
        stages = self.env["crm.stage"].sudo().search([], order="sequence asc, id asc")
        teams = self.env["crm.team"].sudo().search([], order="id asc")
        users = self.env["res.users"].sudo().search([("share", "=", False)], order="name asc", limit=100)
        tags = self.env["crm.tag"].sudo().search([], order="name asc", limit=100)
        segments = self.env["mf.automation.segment"].sudo().search([("active", "=", True)], order="name asc")
        templates = self.env["mail.template"].sudo().search([("model", "in", ("crm.lead", "res.partner"))], limit=100)
        automations = self.sudo().search([("id", "!=", self.id)], order="name asc", limit=100)
        validation = self.action_validate_flow()

        return {
            "automation": {
                "id": self.id,
                "name": self.name,
                "description": self.description or "",
                "category": self.category or "Comercial",
                "state": self.state,
                "model_name": self.model_name,
                "trigger_type": self.trigger_type,
                "trigger_field_name": self.trigger_field_name or "",
                "trigger_activity_state": self.trigger_activity_state,
                "trigger_won_lost": self.trigger_won_lost,
                "trigger_pipeline_id": self.trigger_pipeline_id.id or False,
                "trigger_stage_id": self.trigger_stage_id.id or False,
                "trigger_stage_from_id": self.trigger_stage_from_id.id or False,
                "trigger_segment_id": self.trigger_segment_id.id or False,
                "trigger_inactivity_days": self.trigger_inactivity_days,
                "webhook_token": self.webhook_token,
                "entry_filter": self.env["mf.automation.evaluator"].parse_rules(self.entry_filter_json),
                "stay_filter": self.env["mf.automation.evaluator"].parse_rules(self.stay_filter_json),
                "exit_event": self.exit_event,
                "allow_concurrent": self.allow_concurrent,
                "reentry_policy": self.reentry_policy,
                "reentry_interval_hours": self.reentry_interval_hours,
                "cancel_pending_on_exit": self.cancel_pending_on_exit,
                "published_version_number": self.published_version_number or 0,
            },
            "nodes": [
                {
                    "id": n.id,
                    "node_key": n.node_key,
                    "name": n.name,
                    "category": n.category,
                    "node_type": n.node_type,
                    "pos_x": n.pos_x,
                    "pos_y": n.pos_y,
                    "config": n.get_config(),
                }
                for n in self.node_ids
            ],
            "edges": [
                {
                    "id": e.id,
                    "source_key": e.source_node_id.node_key,
                    "target_key": e.target_node_id.node_key,
                    "branch": e.branch,
                    "label": e.label or "",
                }
                for e in self.edge_ids
            ],
            "validation": validation,
            "preview_paths": self.get_flow_preview(),
            "catalog": {
                "stages": [{"id": s.id, "name": s.name, "team_ids": s.team_ids.ids if "team_ids" in s._fields else [], "is_won": bool(s.is_won)} for s in stages],
                "teams": [{"id": t.id, "name": t.name} for t in teams],
                "users": [{"id": u.id, "name": u.name} for u in users],
                "tags": [{"id": t.id, "name": t.name} for t in tags],
                "segments": [{"id": s.id, "name": s.name, "model_name": s.model_name} for s in segments],
                "mail_templates": [{"id": t.id, "name": t.name, "model": t.model} for t in templates],
                "automations": [{"id": a.id, "name": a.name} for a in automations],
                "trigger_types": [{"code": k, "label": v} for k, v in TRIGGER_TYPES],
                "node_types": [{"code": k, "label": v} for k, v in NODE_TYPES],
            },
        }

    def save_builder_graph(self, payload):
        self.ensure_one()
        data = payload or {}
        auto_vals = {}
        meta = data.get("automation") or {}
        for key in (
            "name", "description", "category", "model_name", "trigger_type",
            "trigger_field_name", "trigger_activity_state", "trigger_won_lost",
            "trigger_pipeline_id", "trigger_stage_id", "trigger_stage_from_id",
            "trigger_segment_id", "trigger_inactivity_days", "exit_event",
            "allow_concurrent", "reentry_policy", "reentry_interval_hours",
            "cancel_pending_on_exit",
        ):
            if key in meta:
                auto_vals[key] = meta[key]
        if "entry_filter" in meta:
            auto_vals["entry_filter_json"] = json.dumps(meta["entry_filter"] or {"logic": "AND", "rules": []})
        if "stay_filter" in meta:
            auto_vals["stay_filter_json"] = json.dumps(meta["stay_filter"] or {"logic": "AND", "rules": []})
        if auto_vals:
            self.write(auto_vals)

        Node = self.env["mf.automation.node"]
        Edge = self.env["mf.automation.edge"]
        existing_by_key = {n.node_key: n for n in self.node_ids}
        keep_keys = set()
        key_to_node = {}

        for item in data.get("nodes") or []:
            key = item.get("node_key") or f"n_{uuid.uuid4().hex[:8]}"
            keep_keys.add(key)
            vals = {
                "name": item.get("name") or "Bloco",
                "category": item.get("category") or "crm_action",
                "node_type": item.get("node_type") or "log_note",
                "pos_x": int(item.get("pos_x") or 80),
                "pos_y": int(item.get("pos_y") or 80),
                "config_json": json.dumps(item.get("config") or {}),
            }
            if key in existing_by_key:
                existing_by_key[key].write(vals)
                key_to_node[key] = existing_by_key[key]
            else:
                vals.update({"automation_id": self.id, "node_key": key})
                key_to_node[key] = Node.create(vals)

        self.edge_ids.unlink()
        to_remove = self.node_ids.filtered(lambda n: n.node_key not in keep_keys)
        if to_remove:
            to_remove.unlink()

        seen_edges = set()
        for e in data.get("edges") or []:
            src = key_to_node.get(e.get("source_key"))
            tgt = key_to_node.get(e.get("target_key"))
            branch = e.get("branch") or "default"
            if src and tgt and src.id != tgt.id:
                sig = (src.id, tgt.id, branch)
                if sig not in seen_edges:
                    seen_edges.add(sig)
                    Edge.create({
                        "automation_id": self.id,
                        "source_node_id": src.id,
                        "target_node_id": tgt.id,
                        "branch": branch,
                        "label": e.get("label") or "",
                    })

        return self.get_builder_data()
