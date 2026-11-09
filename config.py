"""Configuração do bridge — tudo via variáveis de ambiente (Easypanel > Ambiente).

Itens marcados com VALIDAR dependem da instalação do IXC da MaisFibra e devem ser
conferidos uma vez (ver README, seção "Validação no IXC").
"""
import json
import os
from zoneinfo import ZoneInfo


def _json_env(name, default):
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        raise RuntimeError(f"Variável {name} não é um JSON válido")


# ---------------- Odoo ----------------
ODOO_URL = os.getenv("ODOO_URL", "http://localhost:8069")
ODOO_DB = os.getenv("ODOO_DB", "odoo_db")
ODOO_USER = os.getenv("ODOO_USER", "admin")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "admin_pass")
# 1 = módulo maisfibra_crm instalado (usa campos mf_*). 0 = modo antigo (só marcadores em texto).
MF_CAMPOS = os.getenv("MF_CAMPOS", "1") == "1"

# ---------------- IXC ----------------
IXC_HOST = os.getenv("IXC_HOST", "https://ixc.maisfibranet.com.br").rstrip("/")
IXC_USER = os.getenv("IXC_USER", "")
IXC_PASS = os.getenv("IXC_PASS", "")

# Nomes das tabelas (rota /webservice/v1/<tabela>).
IXC_TB_CLIENTE = os.getenv("IXC_TB_CLIENTE", "cliente")          # confirmado
IXC_TB_CONTATO = os.getenv("IXC_TB_CONTATO", "contato")          # confirmado (já usado)
IXC_TB_CIDADE = os.getenv("IXC_TB_CIDADE", "cidade")             # confirmado
IXC_TB_CONDOMINIO = os.getenv("IXC_TB_CONDOMINIO", "condominio")           # VALIDAR
IXC_TB_NEGOCIACAO = os.getenv("IXC_TB_NEGOCIACAO", "crm_negociacoes")      # VALIDAR
IXC_TB_PLANO = os.getenv("IXC_TB_PLANO", "crm_planos_negociacao")          # VALIDAR

# Campos fixos exigidos pelo IXC ao inserir cliente/prospect (filial, tipo de cliente etc.).
# VALIDAR: copie os valores de um prospect já cadastrado na MaisFibra.
IXC_CLIENTE_DEFAULTS = _json_env("IXC_CLIENTE_DEFAULTS", {
    "filial_id": "1",
    "id_tipo_cliente": "1",
    "tipo_assinante": "3",
    "tipo_cliente_scm": "14",
    "cond_pagamento": "1",
    "tipo_localidade": "U",
    "contribuinte_icms": "N",
    "participa_cobranca": "S",
})
# O que faz um cliente ser "prospect" no IXC. VALIDAR o código de status_prospeccao.
IXC_PROSPECT_FIELDS = _json_env("IXC_PROSPECT_FIELDS", {
    "crm": "S",
    "status_prospeccao": "P",
    "ativo": "N",
})
# Campos extras fixos da negociação (ex.: id do funil/etapa inicial no IXC). VALIDAR.
IXC_NEGOCIACAO_DEFAULTS = _json_env("IXC_NEGOCIACAO_DEFAULTS", {})

# ---------------- Segurança das rotas ----------------
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")   # formulários / OPA / Chatwoot
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "")       # chamadas vindas do Odoo

# ---------------- Funis ----------------
FUNIL_ODOO = {
    "B2B": int(os.getenv("FUNIL_B2B", "6")),
    "B2C": int(os.getenv("FUNIL_B2C", "5")),
    "RETENCAO": int(os.getenv("FUNIL_RETENCAO", "4")),
}

# ---------------- Polling ----------------
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "2"))
CADASTROS_INTERVAL_MINUTES = int(os.getenv("CADASTROS_INTERVAL_MINUTES", "60"))
DELTA_LOOKBACK_HOURS = int(os.getenv("DELTA_LOOKBACK_HOURS", "48"))
DELTA_LOOKBACK_DAYS = int(os.getenv("DELTA_LOOKBACK_DAYS", "7"))
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "100"))
MAX_PROCESSADOS = int(os.getenv("MAX_PROCESSADOS", "20000"))

# Monte um VOLUME do Easypanel em /app/data, senão o estado some a cada deploy.
SYNC_STATE_PATH = os.getenv("SYNC_STATE_PATH", "/app/data/ixc_sync_state.json")

TZ_BRASIL = ZoneInfo("America/Sao_Paulo")
