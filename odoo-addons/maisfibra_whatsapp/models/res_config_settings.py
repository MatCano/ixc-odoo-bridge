from markupsafe import Markup
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    wa_provider = fields.Selection(
        [
            ("chromium", "WhatsApp Web via Chromium"),
            ("evolution", "Evolution API"),
            ("meta", "Meta Cloud API (oficial)")
        ],
        string="Provedor de WhatsApp",
        default="chromium",
        config_parameter="maisfibra_whatsapp.provider",
    )
    wa_chromium_url = fields.Char(
        "URL do Serviço Chromium",
        default="http://host.docker.internal:3001",
        config_parameter="maisfibra_whatsapp.chromium_url",
    )

    # Estado da sessão individual do usuário logado
    wa_session_status_raw = fields.Char("Status da Sessão", compute="_compute_session_info")
    wa_session_status_label = fields.Char("Status da Conexão", compute="_compute_session_info")
    wa_session_phone = fields.Char("Telefone Conectado", compute="_compute_session_info")
    wa_session_pushname = fields.Char("Perfil WhatsApp", compute="_compute_session_info")
    wa_session_qr_html = fields.Html("QR Code de Autenticação", compute="_compute_session_info")

    # Configurações legadas mantidas
    wa_evolution_url = fields.Char("URL da Evolution", config_parameter="maisfibra_whatsapp.evolution_url")
    wa_evolution_apikey = fields.Char("API Key da Evolution", config_parameter="maisfibra_whatsapp.evolution_apikey")
    wa_evolution_instance = fields.Char("Instância", config_parameter="maisfibra_whatsapp.evolution_instance")
    wa_webhook_token = fields.Char("Token do webhook", config_parameter="maisfibra_whatsapp.webhook_token")
    wa_default_team_id = fields.Many2one(
        "crm.team", string="Funil para leads novos do WhatsApp",
        config_parameter="maisfibra_whatsapp.default_team_id",
    )

    wa_auto_boas_vindas = fields.Boolean("Boas-vindas automática para lead novo",
                                         config_parameter="maisfibra_whatsapp.auto_boas_vindas")
    wa_msg_boas_vindas = fields.Char(
        "Mensagem de boas-vindas", config_parameter="maisfibra_whatsapp.msg_boas_vindas",
        default="Olá, {nome}! Obrigado pelo contato com a Mais Fibra. Em breve {vendedor} vai falar com você. 🚀",
    )
    wa_auto_estagio = fields.Boolean("Mensagem ao mudar de estágio",
                                     config_parameter="maisfibra_whatsapp.auto_estagio")
    wa_bot_ativo = fields.Boolean("Chatbot ativo", config_parameter="maisfibra_whatsapp.bot_ativo")
    wa_bot_menu = fields.Char("Menu do chatbot (vazio = padrão)", config_parameter="maisfibra_whatsapp.bot_menu")
    wa_bot_pausa_horas = fields.Integer("Pausar o bot após atendimento humano (horas)", default=12,
                                        config_parameter="maisfibra_whatsapp.bot_pausa_horas")

    wa_meta_phone_number_id = fields.Char("Phone Number ID (Meta)", config_parameter="maisfibra_whatsapp.meta_phone_number_id")
    wa_meta_token = fields.Char("Token permanente (Meta)", config_parameter="maisfibra_whatsapp.meta_token")
    wa_meta_verify_token = fields.Char("Verify token do webhook (Meta)", config_parameter="maisfibra_whatsapp.meta_verify_token")
    wa_meta_app_secret = fields.Char("App secret (Meta)", config_parameter="maisfibra_whatsapp.meta_app_secret",
                                     help="Usado para conferir a assinatura X-Hub-Signature-256 das mensagens recebidas.")
    wa_meta_api_version = fields.Char("Versão da Graph API", default="v21.0",
                                       config_parameter="maisfibra_whatsapp.meta_api_version")

    @api.depends("wa_provider")
    def _compute_session_info(self):
        provider = self.env["maisfibra.whatsapp.provider"]
        for record in self:
            session_data = provider.get_user_session_status(self.env.user.id)
            raw_status = (session_data.get("status") or "disconnected").lower()
            record.wa_session_status_raw = raw_status
            record.wa_session_phone = session_data.get("phone") or "—"
            record.wa_session_pushname = session_data.get("pushname") or "—"

            status_map = {
                "ready": "Conectado",
                "connected": "Conectado",
                "qr_ready": "Aguardando leitura do QR Code",
                "awaiting_qr": "Aguardando leitura do QR Code",
                "initializing": "Iniciando Chromium...",
                "authenticated": "Autenticado (sincronizando)",
                "disconnected": "Desconectado",
                "auth_failure": "Falha na Autenticação"
            }
            record.wa_session_status_label = status_map.get(raw_status, raw_status.capitalize())

            qr_code = session_data.get("qrCode")
            if qr_code and raw_status in ("qr_ready", "awaiting_qr"):
                html = f"""
                <div id="wa_qr_container" style="text-align: center; padding: 15px; background: #ffffff; border: 1px solid #dee2e6; border-radius: 8px; max-width: 320px;">
                    <img src="{qr_code}" alt="QR Code WhatsApp" style="width: 250px; height: 250px; display: block; margin: 0 auto;" />
                    <p style="margin-top: 10px; font-weight: 500; color: #1f2937;">Abra o WhatsApp no celular > Aparelhos conectados > Conectar aparelho e aponte para o código.</p>
                    <div id="wa_sync_badge" style="font-size: 12px; color: #6b7280; display: flex; align-items: center; justify-content: center; gap: 6px;">
                        <span class="spinner-grow spinner-grow-sm text-primary" role="status" style="width: 10px; height: 10px;"></span>
                        Aguardando sincronização automática...
                    </div>
                    <script>
                    (function() {{
                        if (window._waPollingInterval) clearInterval(window._waPollingInterval);
                        window._waPollingInterval = setInterval(async function() {{
                            try {{
                                const res = await fetch('/maisfibra/whatsapp/session_status');
                                if (res.ok) {{
                                    const data = await res.json();
                                    const st = (data.status || '').toLowerCase();
                                    if (st === 'ready' || st === 'connected') {{
                                        clearInterval(window._waPollingInterval);
                                        const badge = document.getElementById('wa_sync_badge');
                                        if (badge) {{
                                            badge.innerHTML = '<b style="color: #16a34a;">✓ WhatsApp Conectado! Atualizando tela...</b>';
                                        }}
                                        const refreshBtn = document.querySelector('button[name="action_refresh_whatsapp_status"]');
                                        if (refreshBtn) {{
                                            refreshBtn.click();
                                        }} else {{
                                            window.location.reload();
                                        }}
                                    }}
                                }}
                            }} catch(e) {{}}
                        }}, 2000);
                    }})();
                    </script>
                </div>
                """
            elif raw_status in ("ready", "connected"):
                phone = session_data.get("phone") or "Conectado"
                pushname = session_data.get("pushname") or ""
                html = f"""
                <div class="alert alert-success" style="max-width: 500px; margin-bottom: 0;">
                    <h5 style="margin-bottom: 5px;">✓ Sessão Ativa</h5>
                    <p style="margin-bottom: 0;">WhatsApp conectado com sucesso para <b>{self.env.user.name}</b> ({phone} {pushname}). Suas mensagens e contatos estão sincronizados.</p>
                </div>
                """
            elif raw_status == "initializing":
                html = """
                <div class="alert alert-info" style="max-width: 500px; margin-bottom: 0;">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span class="spinner-border spinner-border-sm text-primary" role="status"></span>
                        <span>Iniciando o navegador Chromium em segundo plano... O QR Code será exibido automaticamente em instantes.</span>
                    </div>
                    <script>
                    (function() {
                        if (window._waPollingInterval) clearInterval(window._waPollingInterval);
                        window._waPollingInterval = setInterval(async function() {
                            try {
                                const res = await fetch('/maisfibra/whatsapp/session_status');
                                if (res.ok) {
                                    const data = await res.json();
                                    const st = (data.status || '').toLowerCase();
                                    if (st !== 'initializing') {
                                        clearInterval(window._waPollingInterval);
                                        const refreshBtn = document.querySelector('button[name="action_refresh_whatsapp_status"]');
                                        if (refreshBtn) refreshBtn.click();
                                        else window.location.reload();
                                    }
                                }
                            } catch(e) {}
                        }, 1500);
                    })();
                    </script>
                </div>
                """
            else:
                html = """
                <div class="alert alert-warning" style="max-width: 500px; margin-bottom: 0;">
                    <p style="margin-bottom: 0;">Nenhuma sessão ativa para seu usuário. Clique no botão <b>"Conectar / Gerar QR Code"</b> para iniciar sua instância exclusiva do WhatsApp Web via Chromium.</p>
                </div>
                """
            record.wa_session_qr_html = Markup(html)

    def action_start_whatsapp_session(self):
        self.ensure_one()
        provider = self.env["maisfibra.whatsapp.provider"]
        provider.start_user_session(self.env.user.id)
        return {
            "type": "ir.actions.client",
            "tag": "reload",
        }

    def action_disconnect_whatsapp_session(self):
        self.ensure_one()
        provider = self.env["maisfibra.whatsapp.provider"]
        provider.disconnect_user_session(self.env.user.id)
        return {
            "type": "ir.actions.client",
            "tag": "reload",
        }

    def action_sync_whatsapp_contacts(self):
        self.ensure_one()
        provider = self.env["maisfibra.whatsapp.provider"]
        contacts = provider.sync_user_contacts(self.env.user.id)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Sincronização de Contatos",
                "message": f"{len(contacts)} contatos sincronizados com sucesso e espelhados no painel de Mensagens Diretas.",
                "type": "success",
                "sticky": False,
            }
        }

    def action_refresh_whatsapp_status(self):
        return {
            "type": "ir.actions.client",
            "tag": "reload",
        }
