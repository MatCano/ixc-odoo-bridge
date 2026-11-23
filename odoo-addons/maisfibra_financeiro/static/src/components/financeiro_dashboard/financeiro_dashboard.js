/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const STATE_LABELS = {
    criada: "Criada",
    registrada: "Registrada",
    enviada: "Enviada",
    paga: "Paga",
    vencida: "Vencida",
    cancelada: "Cancelada",
    falha: "Com Falha",
};

const STATE_COLORS = {
    criada: "secondary",
    registrada: "info",
    enviada: "primary",
    paga: "success",
    vencida: "danger",
    cancelada: "dark",
    falha: "danger",
};

export class MaisFibraFinanceiroDashboard extends Component {
    static template = "maisfibra_financeiro.FinanceiroDashboard";
    static props = { ...standardActionServiceProps };

    setup() {
        this.action = useService("action");
        this.notification = useService("notification");
        this.orm = useService("orm");

        const hoje = new Date();
        const mesAtual = `${hoje.getFullYear()}-${String(hoje.getMonth() + 1).padStart(2, "0")}`;

        this.state = useState({
            loading: true,
            competencia: mesAtual,
            data: {
                kpis: {},
                distribuicao: {},
                recentes: [],
                excecoes: [],
                saude_logs: [],
                config: {},
            },
        });

        onWillStart(async () => {
            await this.carregarDados();
        });
    }

    async carregarDados() {
        this.state.loading = true;
        try {
            const res = await rpc("/maisfibra/financeiro/dashboard_data", {
                competencia: this.state.competencia || "todas",
            });
            this.state.data = res || {};
        } catch (err) {
            this.notification.add("Falha ao carregar dados do Painel Financeiro: " + (err.message || err), {
                type: "danger",
            });
        } finally {
            this.state.loading = false;
        }
    }

    async onChangeCompetencia(ev) {
        this.state.competencia = ev.target.value || "todas";
        await this.carregarDados();
    }

    async verTodasCompetencias() {
        this.state.competencia = "todas";
        await this.carregarDados();
    }

    formatBRL(valor) {
        return Number(valor || 0).toLocaleString("pt-BR", {
            style: "currency",
            currency: "BRL",
        });
    }

    getLabelStatus(st) {
        return STATE_LABELS[st] || st;
    }

    getColorStatus(st) {
        return STATE_COLORS[st] || "secondary";
    }

    getBarWidth(count) {
        const total = this.state.data?.kpis?.total_cobrancas || 0;
        if (!total) return "0%";
        return `${Math.round((count / total) * 100)}%`;
    }

    abrirListaCobrancas(domain = [], title = "Boletos e Cobranças") {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: title,
            res_model: "maisfibra.cobranca",
            view_mode: "list,kanban,form",
            views: [[false, "list"], [false, "kanban"], [false, "form"]],
            domain,
        });
    }

    abrirCobranca(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "maisfibra.cobranca",
            res_id: id,
            view_mode: "form",
            views: [[false, "form"]],
        });
    }

    abrirNovaCobranca() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Nova Cobrança",
            res_model: "maisfibra.cobranca",
            view_mode: "form",
            views: [[false, "form"]],
        });
    }

    abrirGerarLote() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Gerar Cobranças Recorrentes em Lote",
            res_model: "maisfibra.cobranca.lote.wizard",
            view_mode: "form",
            views: [[false, "form"]],
            target: "new",
        });
    }

    abrirNotasFiscais(domain = []) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Notas Fiscais (NFCom / NFS-e)",
            res_model: "maisfibra.nota.fiscal",
            view_mode: "list,kanban,form",
            views: [[false, "list"], [false, "kanban"], [false, "form"]],
            domain,
        });
    }

    abrirFilaExcecoes() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Fila de Exceções",
            res_model: "maisfibra.fila.excecao",
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
            domain: [["state", "in", ["pendente", "reprocessando"]]],
        });
    }

    abrirConfiguracoes() {
        this.action.doAction("base_setup.action_general_configuration");
    }

    async sincronizarIXC() {
        try {
            const res = await this.orm.call("maisfibra.cobranca", "action_sincronizar_ixc", []);
            this.notification.add(
                `Sincronização IXC concluída: ${res.criadas || 0} criadas, ${res.atualizadas || 0} atualizadas.`,
                { type: "success" }
            );
            await this.carregarDados();
        } catch (err) {
            this.notification.add("Erro na sincronização com IXC: " + (err.message || err), { type: "warning" });
        }
    }

    async reprocessarExcecao(excId) {
        try {
            await this.orm.call("maisfibra.fila.excecao", "action_reprocessar", [[excId]]);
            this.notification.add("Exceção reprocessada.", { type: "success" });
            await this.carregarDados();
        } catch (err) {
            this.notification.add("Falha ao reprocessar: " + (err.message || err), { type: "danger" });
        }
    }
}

registry.category("actions").add("maisfibra_financeiro.dashboard", MaisFibraFinanceiroDashboard);
