/** @odoo-module **/

import { Chatter } from "@mail/chatter/web_portal/chatter";
import { patch } from "@web/core/utils/patch";
import { LeadWhatsappChat } from "./lead_whatsapp_chat";

Object.assign(Chatter.components, { LeadWhatsappChat });

patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        this.state.mfChatterTab = "whatsapp";
    },

    get isWhatsappLeadChatter() {
        return this.props.threadModel === "crm.lead" && Boolean(this.props.threadId);
    },

    get isWhatsappChatActive() {
        return this.isWhatsappLeadChatter && this.state.mfChatterTab === "whatsapp";
    },

    setWhatsappChatterMode(mode) {
        this.state.mfChatterTab = mode;
        if (mode === "chatter" && this.state.thread?.id) {
            this.load(this.state.thread, ["messages"]);
        }
    },

    onWhatsappMessageSent() {
        if (this.state.thread?.id) {
            this.load(this.state.thread, ["messages"]);
        }
    },
});
