/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const PALETTE_GROUPS = [
    {
        key: "trigger",
        label: "Gatilhos",
        icon: "fa-bolt",
        color: "#714B67",
        items: [{ node_type: "trigger_entry", name: "Gatilho de Entrada", icon: "fa-bolt" }],
    },
    {
        key: "condition",
        label: "Condições & Segmentação",
        icon: "fa-code-fork",
        color: "#0d6efd",
        items: [
            { node_type: "condition_if", name: "Condição (SIM / NÃO)", icon: "fa-code-fork" },
            { node_type: "segment_check", name: "Verificar Segmento", icon: "fa-filter" },
        ],
    },
    {
        key: "crm_action",
        label: "Ações de CRM",
        icon: "fa-briefcase",
        color: "#198754",
        items: [
            { node_type: "move_stage", name: "Mover Etapa / Pipeline", icon: "fa-columns" },
            { node_type: "assign_owner", name: "Alterar Responsável", icon: "fa-user" },
            { node_type: "assign_team", name: "Alterar Equipe / Pipeline", icon: "fa-users" },
            { node_type: "update_field", name: "Atualizar Campo", icon: "fa-pencil-square-o" },
            { node_type: "add_tag", name: "Adicionar Tag", icon: "fa-tag" },
            { node_type: "remove_tag", name: "Remover Tag", icon: "fa-times-circle-o" },
            { node_type: "update_opportunity_metrics", name: "Valor / Prioridade", icon: "fa-line-chart" },
            { node_type: "create_activity", name: "Criar Atividade", icon: "fa-calendar-check-o" },
            { node_type: "create_task", name: "Criar Tarefa", icon: "fa-tasks" },
            { node_type: "log_note", name: "Registrar Nota", icon: "fa-sticky-note-o" },
            { node_type: "create_record", name: "Criar Contato / Lead", icon: "fa-plus-square-o" },
            { node_type: "mark_won", name: "Marcar como Ganha", icon: "fa-trophy" },
            { node_type: "mark_lost", name: "Marcar como Perdida", icon: "fa-ban" },
        ],
    },
    {
        key: "communication",
        label: "Comunicação",
        icon: "fa-comments",
        color: "#00a884",
        items: [
            { node_type: "send_whatsapp", name: "Enviar WhatsApp", icon: "fa-whatsapp" },
            { node_type: "send_email", name: "Enviar E-mail", icon: "fa-envelope-o" },
        ],
    },
    {
        key: "wait",
        label: "Esperas & Agendamentos",
        icon: "fa-clock-o",
        color: "#fd7e14",
        items: [
            { node_type: "wait_duration", name: "Aguardar Tempo", icon: "fa-clock-o" },
            { node_type: "wait_reply", name: "Aguardar Resposta", icon: "fa-reply" },
            { node_type: "wait_stage", name: "Aguardar Etapa", icon: "fa-step-forward" },
            { node_type: "wait_activity", name: "Aguardar Atividade", icon: "fa-check-circle-o" },
            { node_type: "wait_condition", name: "Aguardar Condição", icon: "fa-hourglass-half" },
            { node_type: "wait_time_window", name: "Janela de Horário", icon: "fa-calendar" },
        ],
    },
    {
        key: "integration",
        label: "Integrações",
        icon: "fa-plug",
        color: "#6f42c1",
        items: [{ node_type: "call_webhook", name: "Enviar Webhook HTTP", icon: "fa-plug" }],
    },
    {
        key: "flow_control",
        label: "Controle de Fluxo",
        icon: "fa-sitemap",
        color: "#495057",
        items: [
            { node_type: "start_automation", name: "Iniciar Outra Automação", icon: "fa-share-alt" },
            { node_type: "end_flow", name: "Encerrar Fluxo", icon: "fa-stop-circle-o" },
        ],
    },
];

const BRANCH_COLORS = {
    default: "#6c757d",
    true: "#198754",
    false: "#dc3545",
    timeout: "#fd7e14",
    reply: "#0d6efd",
};

const OPERATORS = [
    { code: "eq", label: "É igual a" },
    { code: "neq", label: "É diferente de" },
    { code: "contains", label: "Contém" },
    { code: "not_contains", label: "Não contém" },
    { code: "is_set", label: "Está preenchido" },
    { code: "is_empty", label: "Está vazio" },
    { code: "gt", label: "É maior que" },
    { code: "lt", label: "É menor que" },
    { code: "between", label: "Está entre" },
    { code: "before", label: "É anterior a" },
    { code: "after", label: "É posterior a" },
    { code: "in_last_days", label: "Nos últimos X dias" },
    { code: "not_in_last_days", label: "Não ocorreu nos últimos X dias" },
    { code: "in_segment", label: "Pertence ao segmento" },
    { code: "not_in_segment", label: "Não pertence ao segmento" },
];

const TEMPLATE_VARS = [
    "{{contact_name}}",
    "{{company_name}}",
    "{{owner_name}}",
    "{{lead_name}}",
    "{{expected_revenue}}",
    "{{stage_name}}",
    "{{phone}}",
    "{{email}}",
];

export class AutomationBuilder extends Component {
    static template = "maisfibra_automacao.AutomationBuilder";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.palette = PALETTE_GROUPS;
        this.operators = OPERATORS;
        this.templateVars = TEMPLATE_VARS;

        this.state = useState({
            loading: true,
            saving: false,
            automationId: null,
            automation: {},
            nodes: [],
            edges: [],
            validation: { valid: true, errors: [], node_errors: {} },
            previewPaths: [],
            catalog: { stages: [], teams: [], users: [], tags: [], segments: [], mail_templates: [], automations: [], trigger_types: [] },
            selectedNodeKey: null,
            connectingFrom: null, // { nodeKey, branch }
            showPreviewModal: false,
        });

        this._drag = null;

        onWillStart(async () => {
            await this.initLoad();
        });
    }

    async initLoad() {
        const params = this.props.action?.params || {};
        const ctx = this.props.action?.context || {};
        let autoId = params.automation_id || ctx.active_id;
        if (!autoId) {
            const list = await this.orm.searchRead("mf.automation", [], ["id"], { limit: 1, order: "id desc" });
            if (list.length) {
                autoId = list[0].id;
            }
        }
        this.state.automationId = autoId || null;
        if (autoId) {
            await this.loadData(autoId);
        } else {
            this.state.loading = false;
        }
    }

    async loadData(autoId = this.state.automationId) {
        if (!autoId) return;
        this.state.loading = true;
        try {
            const data = await this.orm.call("mf.automation", "get_builder_data", [[autoId]]);
            this._applyBuilderData(data);
        } catch (err) {
            this.notification.add("Erro ao carregar construtor: " + (err?.data?.message || err.message), { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    _applyBuilderData(data) {
        this.state.automation = data.automation || {};
        this.state.nodes = (data.nodes || []).map((n) => ({
            ...n,
            config: n.config || {},
        }));
        this.state.edges = data.edges || [];
        this.state.validation = data.validation || { valid: true, errors: [], node_errors: {} };
        this.state.previewPaths = data.preview_paths || [];
        this.state.catalog = data.catalog || {};
        if (!this.state.selectedNodeKey && this.state.nodes.length) {
            this.state.selectedNodeKey = this.state.nodes[0].node_key;
        }
    }

    get selectedNode() {
        return this.state.nodes.find((n) => n.node_key === this.state.selectedNodeKey) || null;
    }

    get categoryColorMap() {
        const map = {};
        for (const grp of PALETTE_GROUPS) {
            map[grp.key] = grp.color;
        }
        return map;
    }

    get svgEdges() {
        const byKey = {};
        for (const n of this.state.nodes) {
            byKey[n.node_key] = n;
        }
        return this.state.edges
            .map((e, idx) => {
                const src = byKey[e.source_key];
                const tgt = byKey[e.target_key];
                if (!src || !tgt) return null;
                const x1 = Number(src.pos_x || 0) + 110;
                const y1 = Number(src.pos_y || 0) + 64;
                const x2 = Number(tgt.pos_x || 0) + 110;
                const y2 = Number(tgt.pos_y || 0);
                const dy = Math.max(40, Math.abs(y2 - y1) * 0.5);
                const d = `M ${x1} ${y1} C ${x1} ${y1 + dy}, ${x2} ${y2 - dy}, ${x2} ${y2}`;
                const midX = (x1 + x2) / 2;
                const midY = (y1 + y2) / 2;
                return {
                    key: `edge_${idx}_${e.source_key}_${e.target_key}_${e.branch}`,
                    index: idx,
                    d,
                    midX,
                    midY,
                    branch: e.branch || "default",
                    color: BRANCH_COLORS[e.branch] || BRANCH_COLORS.default,
                    label: (e.branch || "default").toUpperCase(),
                };
            })
            .filter(Boolean);
    }

    getNodeErrors(nodeKey) {
        return (this.state.validation?.node_errors || {})[nodeKey] || [];
    }

    getBranchesForNode(node) {
        if (!node) return [];
        if (node.node_type === "condition_if" || node.node_type === "segment_check") {
            return [
                { code: "true", label: "+ SIM", cls: "btn-outline-success" },
                { code: "false", label: "+ NÃO", cls: "btn-outline-danger" },
            ];
        }
        if (node.category === "wait") {
            if (node.node_type === "wait_reply") {
                return [
                    { code: "reply", label: "+ Resposta", cls: "btn-outline-primary" },
                    { code: "timeout", label: "+ Timeout", cls: "btn-outline-warning" },
                ];
            }
            return [
                { code: "default", label: "+ Próximo", cls: "btn-outline-secondary" },
                { code: "timeout", label: "+ Timeout", cls: "btn-outline-warning" },
            ];
        }
        if (node.node_type === "end_flow") {
            return [];
        }
        return [{ code: "default", label: "+ Conectar", cls: "btn-outline-primary" }];
    }

    addNodeFromPalette(category, item) {
        const idx = this.state.nodes.length + 1;
        const key = `n_${Date.now().toString(36)}_${idx}`;
        const col = idx % 3;
        const row = Math.floor((idx - 1) / 3);
        const defaultConfig = {};
        if (item.node_type === "condition_if" || item.node_type === "wait_condition") {
            defaultConfig.rules = { logic: "AND", rules: [{ field: "phone", operator: "is_set", value: "" }] };
        } else if (item.node_type === "wait_duration") {
            defaultConfig.amount = 1;
            defaultConfig.unit = "days";
        } else if (item.node_type === "log_note") {
            defaultConfig.body = "Nota automática para {{lead_name}}";
        } else if (item.node_type === "send_whatsapp") {
            defaultConfig.body = "Olá {{contact_name}}, aqui é da Mais Fibra!";
        } else if (item.node_type === "send_email") {
            defaultConfig.subject = "Contato Mais Fibra - {{lead_name}}";
            defaultConfig.body = "Olá {{contact_name}}, obrigado pelo contato!";
        }
        const newNode = {
            node_key: key,
            name: item.name,
            category,
            node_type: item.node_type,
            pos_x: 80 + col * 270,
            pos_y: 120 + row * 140,
            config: defaultConfig,
        };
        this.state.nodes.push(newNode);
        this.state.selectedNodeKey = key;
    }

    selectNode(nodeKey) {
        if (this.state.connectingFrom) {
            const { nodeKey: srcKey, branch } = this.state.connectingFrom;
            this.state.connectingFrom = null;
            if (srcKey === nodeKey) {
                this.notification.add("Um bloco não pode se conectar a si mesmo.", { type: "warning" });
                return;
            }
            const exists = this.state.edges.some(
                (e) => e.source_key === srcKey && e.target_key === nodeKey && e.branch === branch
            );
            if (exists) {
                this.notification.add("Conexão já existente neste ramo.", { type: "warning" });
                return;
            }
            this.state.edges.push({ source_key: srcKey, target_key: nodeKey, branch, label: branch.toUpperCase() });
            return;
        }
        this.state.selectedNodeKey = nodeKey;
    }

    startConnect(nodeKey, branch, ev) {
        ev.stopPropagation();
        this.state.connectingFrom = { nodeKey, branch };
        this.notification.add(`Clique no bloco de destino para conectar o ramo [${branch.toUpperCase()}].`, {
            type: "info",
        });
    }

    cancelConnect() {
        this.state.connectingFrom = null;
    }

    removeEdge(idx) {
        this.state.edges.splice(idx, 1);
    }

    duplicateNode(nodeKey, ev) {
        ev.stopPropagation();
        const orig = this.state.nodes.find((n) => n.node_key === nodeKey);
        if (!orig) return;
        const copyKey = `n_${Date.now().toString(36)}`;
        this.state.nodes.push({
            node_key: copyKey,
            name: `${orig.name} (cópia)`,
            category: orig.category,
            node_type: orig.node_type,
            pos_x: Number(orig.pos_x || 80) + 40,
            pos_y: Number(orig.pos_y || 80) + 40,
            config: JSON.parse(JSON.stringify(orig.config || {})),
        });
        this.state.selectedNodeKey = copyKey;
    }

    removeNode(nodeKey, ev) {
        ev.stopPropagation();
        const target = this.state.nodes.find((n) => n.node_key === nodeKey);
        if (target?.category === "trigger" && this.state.nodes.filter((n) => n.category === "trigger").length <= 1) {
            this.notification.add("O fluxo precisa manter pelo menos 1 bloco de Gatilho.", { type: "warning" });
            return;
        }
        this.state.nodes = this.state.nodes.filter((n) => n.node_key !== nodeKey);
        this.state.edges = this.state.edges.filter((e) => e.source_key !== nodeKey && e.target_key !== nodeKey);
        if (this.state.selectedNodeKey === nodeKey) {
            this.state.selectedNodeKey = this.state.nodes[0]?.node_key || null;
        }
    }

    onNodeMouseDown(nodeKey, ev) {
        if (ev.target.closest("button")) return;
        const node = this.state.nodes.find((n) => n.node_key === nodeKey);
        if (!node) return;
        this._drag = {
            nodeKey,
            startX: ev.clientX,
            startY: ev.clientY,
            origX: Number(node.pos_x || 80),
            origY: Number(node.pos_y || 80),
        };
        const onMove = (mv) => {
            if (!this._drag) return;
            const dx = mv.clientX - this._drag.startX;
            const dy = mv.clientY - this._drag.startY;
            node.pos_x = Math.max(10, Math.round(this._drag.origX + dx));
            node.pos_y = Math.max(10, Math.round(this._drag.origY + dy));
        };
        const onUp = () => {
            this._drag = null;
            window.removeEventListener("mousemove", onMove);
            window.removeEventListener("mouseup", onUp);
        };
        window.addEventListener("mousemove", onMove);
        window.addEventListener("mouseup", onUp);
    }

    ensureConditionRules(node) {
        if (!node.config) node.config = {};
        if (!node.config.rules || !Array.isArray(node.config.rules.rules)) {
            node.config.rules = { logic: "AND", rules: [] };
        }
        return node.config.rules;
    }

    addConditionRule(node) {
        const group = this.ensureConditionRules(node);
        group.rules.push({ field: "phone", operator: "is_set", value: "" });
    }

    removeConditionRule(node, idx) {
        const group = this.ensureConditionRules(node);
        group.rules.splice(idx, 1);
    }

    insertVariable(node, varText) {
        if (!node.config) node.config = {};
        node.config.body = `${node.config.body || ""}${node.config.body ? " " : ""}${varText}`;
    }

    async saveDraft(silent = false) {
        if (!this.state.automationId) return;
        this.state.saving = true;
        try {
            const payload = {
                automation: this.state.automation,
                nodes: this.state.nodes,
                edges: this.state.edges,
            };
            const data = await this.orm.call("mf.automation", "save_builder_graph", [[this.state.automationId], payload]);
            this._applyBuilderData(data);
            if (!silent) {
                this.notification.add("Rascunho salvo com sucesso.", { type: "success" });
            }
            return data;
        } catch (err) {
            this.notification.add("Erro ao salvar fluxo: " + (err?.data?.message || err.message), { type: "danger" });
            return null;
        } finally {
            this.state.saving = false;
        }
    }

    async validateFlow() {
        const data = await this.saveDraft(true);
        if (!data) return;
        if (data.validation?.valid) {
            this.notification.add("Fluxo validado sem erros! Pronto para publicação.", { type: "success" });
        } else {
            this.notification.add(`${data.validation?.errors?.length || 1} erro(s) encontrado(s) no fluxo.`, { type: "warning" });
        }
    }

    async publishFlow() {
        const data = await this.saveDraft(true);
        if (!data) return;
        try {
            await this.orm.call("mf.automation", "action_publish", [[this.state.automationId]]);
            this.notification.add("Automação publicada e ativada com sucesso!", { type: "success" });
            await this.loadData();
        } catch (err) {
            this.notification.add(err?.data?.message || err.message, { type: "danger", sticky: true });
        }
    }

    async pauseFlow() {
        try {
            await this.orm.call("mf.automation", "action_pause", [[this.state.automationId]]);
            this.notification.add("Automação pausada.", { type: "info" });
            await this.loadData();
        } catch (err) {
            this.notification.add(err?.data?.message || err.message, { type: "danger" });
        }
    }

    togglePreviewModal() {
        this.state.showPreviewModal = !this.state.showPreviewModal;
    }

    goBackToForm() {
        if (!this.state.automationId) return;
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "mf.automation",
            res_id: this.state.automationId,
            view_mode: "form",
            views: [[false, "form"]],
        });
    }
}

registry.category("actions").add("maisfibra_automacao.builder", AutomationBuilder);
