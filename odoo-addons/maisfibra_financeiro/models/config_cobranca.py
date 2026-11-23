"""Configurações de Boletos, Cobranças Recorrentes, Canais, Regras e Fiscal."""
from odoo import fields, models

P = "maisfibra_financeiro."


class ResConfigSettingsCobranca(models.TransientModel):
    _inherit = "res.config.settings"

    # Arquitetura (Seção 4 PDF)
    mf_arquitetura_cobranca = fields.Selection(
        [
            ("a_odoo", "Alternativa A — Odoo como responsável pela cobrança"),
            ("b_ixc", "Alternativa B — IXC como responsável pela cobrança"),
            ("c_hibrido", "Alternativa C — Modelo híbrido (IXC operacional + Odoo acompanhamento/envio)"),
        ],
        string="Arquitetura de Cobrança", default="c_hibrido",
        config_parameter=P + "arquitetura_cobranca",
    )
    mf_sistema_mestre_cobranca = fields.Selection(
        [("ixc", "IXC Provedor"), ("odoo", "Odoo"), ("hibrido", "Híbrido")],
        string="Sistema Mestre (Cobrança/Pagamento)", default="ixc",
        config_parameter=P + "sistema_mestre_cobranca",
    )
    mf_sistema_mestre_fiscal = fields.Selection(
        [("ixc", "IXC Provedor"), ("odoo", "Odoo (Localização BR)"), ("externo", "Sistema Fiscal Externo")],
        string="Sistema Mestre Fiscal", default="ixc",
        config_parameter=P + "sistema_mestre_fiscal",
    )

    # Emissão local / homologação (Alternativa A / fallback)
    mf_banco_codigo = fields.Char("Código do Banco", default="756", config_parameter=P + "banco_codigo")
    mf_banco_nome = fields.Char("Nome do Banco", default="SICOOB", config_parameter=P + "banco_nome")
    mf_banco_agencia = fields.Char("Agência", default="0001", config_parameter=P + "banco_agencia")
    mf_banco_conta = fields.Char("Conta", default="0012345", config_parameter=P + "banco_conta")
    mf_banco_carteira = fields.Char("Carteira", default="109", config_parameter=P + "banco_carteira")
    mf_pix_chave = fields.Char("Chave PIX Recebedora", default="", config_parameter=P + "pix_chave")

    # Regras de negócio de cobrança (Seção 7 PDF)
    mf_multa_percentual = fields.Float("Multa padrão (%)", default=2.0, config_parameter=P + "multa_percentual")
    mf_juros_mensal_percentual = fields.Float("Juros padrão ao mês (%)", default=1.0, config_parameter=P + "juros_mensal_percentual")
    mf_dias_segunda_via = fields.Integer("Prazo padrão 2ª via (dias)", default=5, config_parameter=P + "dias_segunda_via")
    mf_geracao_automatica_ativa = fields.Boolean("Gerar cobranças recorrentes automaticamente", config_parameter=P + "geracao_automatica_ativa")
    mf_emissao_automatica_ativa = fields.Boolean("Emitir boletos automaticamente ao gerar", config_parameter=P + "emissao_automatica_ativa")
    mf_vincular_account_move = fields.Boolean("Gerar fatura contábil (account.move) espelhada", config_parameter=P + "vincular_account_move")

    # Comunicação / Envio (Seção 3.1 & 7 PDF)
    mf_canal_envio_padrao = fields.Selection(
        [("email", "E-mail"), ("whatsapp", "WhatsApp"), ("ambos", "E-mail e WhatsApp")],
        string="Canal Oficial de Envio", default="email",
        config_parameter=P + "canal_envio_padrao",
    )
    mf_envio_automatico_ativo = fields.Boolean("Disparo automático ao emitir boleto", config_parameter=P + "envio_automatico_ativo")
    mf_envio_nf_automatico = fields.Boolean("Enviar Nota Fiscal automaticamente ao autorizar", config_parameter=P + "envio_nf_automatico")
    mf_remetente_email = fields.Char("Remetente padrão (E-mail)", config_parameter=P + "remetente_email")

    # Fiscal (Seção 3.2 & 7 PDF)
    mf_fiscal_ativo = fields.Boolean("Habilitar emissão/sincronização de Notas Fiscais", config_parameter=P + "fiscal_ativo")
    mf_fiscal_tipo_padrao = fields.Selection(
        [("nfcom", "NFCom (Modelo 62 - SCM)"), ("nfse", "NFS-e (Municipal - SVA)"), ("nfe", "NF-e")],
        string="Documento Fiscal Padrão", default="nfcom",
        config_parameter=P + "fiscal_tipo_padrao",
    )
    mf_fiscal_ambiente = fields.Selection(
        [("homologacao", "Homologação"), ("producao", "Produção")],
        string="Ambiente Fiscal", default="homologacao",
        config_parameter=P + "fiscal_ambiente",
    )
    mf_fiscal_fato_gerador = fields.Selection(
        [("manual", "Manual"), ("emissao_boleto", "Na emissão da cobrança"), ("pagamento", "Na confirmação de pagamento")],
        string="Fato Gerador da NF", default="pagamento",
        config_parameter=P + "fiscal_fato_gerador",
    )
    mf_validacao_contabil_confirmada = fields.Boolean(
        "Regras fiscais validadas com a contabilidade",
        config_parameter=P + "validacao_contabil_confirmada",
    )
    mf_aliquota_iss = fields.Float("Alíquota ISS (%)", default=2.0, config_parameter=P + "aliquota_iss")
    mf_aliquota_icms = fields.Float("Alíquota ICMS SCM (%)", default=0.0, config_parameter=P + "aliquota_icms")

    # Integração & Resiliência (Seção 6 PDF)
    mf_sync_ixc_ativa = fields.Boolean("Sincronização periódica IXC ativa", config_parameter=P + "sync_ixc_ativa")
    mf_max_retentativas = fields.Integer("Máximo de retentativas na fila de exceções", default=5, config_parameter=P + "max_retentativas")
    mf_webhook_token = fields.Char("Token de Webhook Financeiro", config_parameter=P + "webhook_token")
