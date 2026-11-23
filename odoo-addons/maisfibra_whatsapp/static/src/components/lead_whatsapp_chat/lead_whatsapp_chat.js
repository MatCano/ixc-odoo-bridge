/** @odoo-module **/

import {
    Component,
    onMounted,
    onWillStart,
    onWillUnmount,
    onWillUpdateProps,
    useRef,
    useState,
} from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";

const MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024;
const MEDIA_PLACEHOLDERS = new Set([
    "[image]",
    "[imagem]",
    "[voice]",
    "[audio]",
    "[áudio]",
    "[video]",
    "[vídeo]",
    "[document]",
    "[documento]",
    "[sticker]",
    "[figurinha]",
]);

export class LeadWhatsappChat extends Component {
    static template = "maisfibra_whatsapp.LeadWhatsappChat";
    static props = {
        leadId: { type: Number },
        isAside: { type: Boolean, optional: true },
        onMessageSent: { type: Function, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.busService = useService("bus_service");
        this.messagesRef = useRef("messages");
        this.fileInputRef = useRef("fileInput");
        this.composerInputRef = useRef("composerInput");

        this.state = useState({
            loading: true,
            sending: false,
            retryingId: null,
            error: "",
            contactName: "",
            phone: "",
            normalizedPhone: "",
            phoneAliases: [],
            partnerId: false,
            canSend: false,
            provider: "chromium",
            sessionStatus: "disconnected",
            messages: [],
            draft: "",
            pendingAttachment: null,
        });

        this._alive = true;
        this.onBusMessage = this.onBusMessage.bind(this);

        onWillStart(async () => {
            await this.loadHistory(this.props.leadId);
        });

        onMounted(() => {
            this.scrollToBottom();
            this.busService.subscribe("maisfibra_whatsapp/message", this.onBusMessage);
        });

        onWillUpdateProps(async (nextProps) => {
            if (nextProps.leadId !== this.props.leadId) {
                this.state.draft = "";
                this.state.pendingAttachment = null;
                this.state.error = "";
                await this.loadHistory(nextProps.leadId);
                this.scrollToBottom();
            }
        });

        onWillUnmount(() => {
            this._alive = false;
            this.busService.unsubscribe("maisfibra_whatsapp/message", this.onBusMessage);
        });
    }

    async loadHistory(leadId = this.props.leadId) {
        if (!leadId) {
            this.state.loading = false;
            this.state.messages = [];
            return;
        }
        this.state.loading = true;
        try {
            const data = await this.orm.call("crm.lead", "get_whatsapp_chat_history", [[leadId]]);
            if (!this._alive || leadId !== this.props.leadId) {
                return;
            }
            this.state.contactName = data.contact_name || data.lead_name || "";
            this.state.phone = data.phone || "";
            this.state.normalizedPhone = data.normalized_phone || "";
            this.state.phoneAliases = data.phone_aliases || [];
            this.state.partnerId = data.partner_id || false;
            this.state.canSend = Boolean(data.can_send);
            this.state.provider = data.provider || "chromium";
            this.state.sessionStatus = data.session_status || "disconnected";
            this.state.messages = data.messages || [];
            this.state.error = "";
        } catch (err) {
            if (!this._alive) {
                return;
            }
            this.state.error = err?.data?.message || err?.message || _t("Não foi possível carregar o histórico de WhatsApp.");
        } finally {
            if (this._alive) {
                this.state.loading = false;
                this.scrollToBottom();
            }
        }
    }

    async onBusMessage(payload) {
        if (!this._alive || !payload) {
            return;
        }
        const sameLead = payload.lead_id && payload.lead_id === this.props.leadId;
        const samePartner = this.state.partnerId && payload.partner_id === this.state.partnerId;
        const samePhone = payload.phone && this.state.phoneAliases.includes(payload.phone);
        if (sameLead || samePartner || samePhone) {
            await this.loadHistory(this.props.leadId);
        }
    }

    scrollToBottom() {
        const scrollNow = () => {
            const el = this.messagesRef.el;
            if (el) {
                el.scrollTop = el.scrollHeight;
            }
        };
        requestAnimationFrame(() => {
            scrollNow();
            setTimeout(scrollNow, 60);
        });
    }

    get avatarInitial() {
        const label = (this.state.contactName || this.state.phone || "W").trim();
        return (label[0] || "W").toUpperCase();
    }

    get sessionBadge() {
        if (this.state.provider !== "chromium") {
            return { text: this.state.provider.toUpperCase(), cls: "o-mf-badge-ok" };
        }
        const status = (this.state.sessionStatus || "").toLowerCase();
        if (status === "connected" || status === "ready") {
            return { text: _t("Conectado"), cls: "o-mf-badge-ok" };
        }
        if (status === "qr" || status === "initializing" || status === "starting") {
            return { text: _t("Conectando"), cls: "o-mf-badge-warn" };
        }
        return { text: _t("Sessão offline"), cls: "o-mf-badge-muted" };
    }

    get canSubmit() {
        return (
            this.state.canSend &&
            !this.state.sending &&
            (Boolean((this.state.draft || "").trim()) || Boolean(this.state.pendingAttachment))
        );
    }

    _formatDateLabel(dateObj) {
        const now = new Date();
        const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
        const startMsg = new Date(dateObj.getFullYear(), dateObj.getMonth(), dateObj.getDate()).getTime();
        const diffDays = Math.round((startToday - startMsg) / 86400000);
        if (diffDays === 0) {
            return _t("Hoje");
        }
        if (diffDays === 1) {
            return _t("Ontem");
        }
        return dateObj.toLocaleDateString("pt-BR");
    }

    _formatTime(dateObj) {
        return dateObj.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
    }

    get groupedMessages() {
        const items = [];
        let lastDateKey = null;
        for (const msg of this.state.messages) {
            const dt = msg.timestamp_ms ? new Date(msg.timestamp_ms) : msg.create_date ? new Date(msg.create_date) : new Date();
            const dateKey = `${dt.getFullYear()}-${dt.getMonth() + 1}-${dt.getDate()}`;
            if (dateKey !== lastDateKey) {
                items.push({
                    isSeparator: true,
                    key: `sep_${dateKey}_${items.length}`,
                    label: this._formatDateLabel(dt),
                });
                lastDateKey = dateKey;
            }
            const rawBody = (msg.body || "").trim();
            const hasMedia = Boolean(msg.media_url);
            const hideBody = hasMedia && MEDIA_PLACEHOLDERS.has(rawBody.toLowerCase());
            const downloadUrl = msg.media_url && msg.media_url.startsWith("/web/content/")
                ? `${msg.media_url}?download=true`
                : msg.media_url;
            items.push({
                ...msg,
                isSeparator: false,
                key: `msg_${msg.id}`,
                timeLabel: this._formatTime(dt),
                displayBody: hideBody ? "" : msg.body || "",
                downloadUrl,
                isImage: hasMedia && (msg.media_type === "image" || msg.media_type === "sticker"),
                isAudio: hasMedia && (msg.media_type === "audio" || msg.media_type === "voice"),
                isVideo: hasMedia && msg.media_type === "video",
                isDocument:
                    hasMedia &&
                    !["image", "sticker", "audio", "voice", "video"].includes(msg.media_type),
            });
        }
        return items;
    }

    _upsertMessage(msg) {
        if (!msg) {
            return;
        }
        const idx = this.state.messages.findIndex((m) => m.id === msg.id);
        if (idx >= 0) {
            this.state.messages[idx] = msg;
        } else {
            this.state.messages.push(msg);
        }
    }

    onComposerKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            if (this.canSubmit) {
                this.onSend();
            }
        }
    }

    onClickAttach() {
        this.fileInputRef.el?.click();
    }

    onFileSelected(ev) {
        const file = ev.target.files?.[0];
        ev.target.value = "";
        if (!file) {
            return;
        }
        if (file.size > MAX_ATTACHMENT_BYTES) {
            this.notification.add(_t("O anexo excede o limite de 15 MB."), { type: "warning" });
            return;
        }
        const reader = new FileReader();
        reader.onload = () => {
            const dataUrl = String(reader.result || "");
            const commaIdx = dataUrl.indexOf(",");
            const base64 = commaIdx >= 0 ? dataUrl.slice(commaIdx + 1) : dataUrl;
            const mime = file.type || "application/octet-stream";
            this.state.pendingAttachment = {
                name: file.name,
                mime,
                base64,
                isImage: mime.startsWith("image/"),
                previewUrl: mime.startsWith("image/") ? dataUrl : null,
            };
        };
        reader.readAsDataURL(file);
    }

    clearAttachment() {
        this.state.pendingAttachment = null;
    }

    async onSend() {
        if (!this.canSubmit) {
            return;
        }
        const body = (this.state.draft || "").trim();
        const attachment = this.state.pendingAttachment;
        this.state.sending = true;
        this.state.error = "";
        try {
            const res = await this.orm.call(
                "crm.lead",
                "action_send_whatsapp_chat_message",
                [[this.props.leadId]],
                {
                    body,
                    media_base64: attachment ? attachment.base64 : false,
                    media_mime: attachment ? attachment.mime : false,
                    media_filename: attachment ? attachment.name : false,
                }
            );
            if (!this._alive) {
                return;
            }
            if (res?.message) {
                this._upsertMessage(res.message);
            }
            if (res?.ok) {
                this.state.draft = "";
                this.state.pendingAttachment = null;
                this.props.onMessageSent?.();
            } else {
                this.state.error = res?.error || _t("Falha ao enviar mensagem pelo WhatsApp.");
            }
            this.scrollToBottom();
        } catch (err) {
            if (!this._alive) {
                return;
            }
            this.state.error = err?.data?.message || err?.message || _t("Erro ao enviar mensagem pelo WhatsApp.");
        } finally {
            if (this._alive) {
                this.state.sending = false;
                this.composerInputRef.el?.focus();
            }
        }
    }

    async onRetry(msgId) {
        if (!msgId || this.state.retryingId) {
            return;
        }
        this.state.retryingId = msgId;
        this.state.error = "";
        try {
            const res = await this.orm.call(
                "crm.lead",
                "action_retry_whatsapp_message",
                [[this.props.leadId], msgId]
            );
            if (!this._alive) {
                return;
            }
            if (res?.message) {
                this._upsertMessage(res.message);
            }
            if (res?.ok) {
                this.props.onMessageSent?.();
            } else {
                this.state.error = res?.error || _t("Falha ao reenviar mensagem.");
            }
        } catch (err) {
            if (!this._alive) {
                return;
            }
            this.state.error = err?.data?.message || err?.message || _t("Erro ao reenviar mensagem.");
        } finally {
            if (this._alive) {
                this.state.retryingId = null;
            }
        }
    }
}
