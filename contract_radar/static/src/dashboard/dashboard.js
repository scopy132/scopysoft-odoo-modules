/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

export class ContractRadarDashboard extends Component {
    static template = "contract_radar.Dashboard";
    static components = { Layout };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.state = useState({ data: null });
        onWillStart(async () => {
            await this.loadData();
        });
    }

    async loadData() {
        this.state.data = await this.orm.call("contract.radar", "get_dashboard_data", []);
    }

    async onRefresh() {
        await this.loadData();
    }

    openList(title, domain) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            name: title,
            res_model: "contract.radar",
            views: [
                [false, "list"],
                [false, "kanban"],
                [false, "form"],
            ],
            domain: domain || [],
            target: "current",
        });
    }

    openRecord(id) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "contract.radar",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openForecast() {
        this.actionService.doAction("contract_radar.action_contract_cost_line");
    }

    urgencyClass(urgency) {
        if (urgency === "urgent" || urgency === "overdue") {
            return "text-bg-danger";
        }
        if (urgency === "soon") {
            return "text-bg-warning";
        }
        return "text-bg-success";
    }
}

registry.category("actions").add("contract_radar.dashboard", ContractRadarDashboard);
