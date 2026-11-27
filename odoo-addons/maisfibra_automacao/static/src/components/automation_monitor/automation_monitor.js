/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const STATE_BADGES = {
    queued: "text-bg-info",
    running: "text-bg-primary",
    waiting: "text-bg-warning",
    done: "text-bg-success",
    failed: "text-bg-danger",
    cancelled: "text-bg-secondary",
};

export class AutomationMonitor extends Component {
    static template = "maisfibra_automacao.AutomationMonitor";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            loading: true,
            stateFilter: "all",
            kpis: {},
            executions: [],
        });

        onWillStart(async () => {
            await this.loadData();
        });
    }

    async loadData() {
        this.state.loading = true;
        try {
            const res = await rpc("/maisfibra/automacao/monitor_data", {
                state_filter: this.state.stateFilter,
            });
            this.state.kpis = res?.kpis || {};
            this.state.executions = res?.executions || [];
        } catch (err) {
            this.notification.add("Erro ao carregar monitoramento: " + (err.message || err), { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    async setFilter(st) {
        this.state.stateFilter = st;
        await this.loadData();
    }

    getBadgeClass(st) {
        return STATE_BADGES[st] || "text-bg-secondary";
    }

    openExecution(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "mf.automation.execution",
            res_id: id,
            view_mode: "form",
            views: [[false, "form"]],
        });
    }

    async retryExecution(id) {
        try {
            await this.orm.call("mf.automation.execution", "action_retry_execution", [[id]]);
            this.notification.add("Execução reprocessada.", { type: "success" });
            await this.loadData();
        } catch (err) {
            this.notification.add(err?.data?.message || err.message, { type: "danger" });
        }
    }

    async cancelExecution(id) {
        try {
            await this.orm.call("mf.automation.execution", "action_cancel_execution", [[id]]);
            this.notification.add("Execução cancelada.", { type: "info" });
            await this.loadData();
        } catch (err) {
            this.notification.add(err?.data?.message || err.message, { type: "danger" });
        }
    }
}

registry.category("actions").add("maisfibra_automacao.monitor", AutomationMonitor);
