import os
import json
import re
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import logging
from fastapi import FastAPI, Header, HTTPException, BackgroundTasks
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
import requests
import xmlrpc.client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("IXC-Odoo-Bridge")

app = FastAPI(title="IXC to Odoo Bridge")

# ==========================================
# Configurações de Ambiente (NUNCA hardcode aqui)
# ==========================================
ODOO_URL = os.getenv("ODOO_URL", "http://localhost:8069")
ODOO_DB = os.getenv("ODOO_DB", "odoo_db")
ODOO_USER = os.getenv("ODOO_USER", "admin")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "admin_pass")

IXC_HOST = os.getenv("IXC_HOST", "https://ixc.maisfibranet.com.br")
IXC_USER = os.getenv("IXC_USER", "")
IXC_PASS = os.getenv("IXC_PASS", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# Funis do Odoo (team_id do crm.lead)
FUNIL_ODOO = {
    "B2B": int(os.getenv("FUNIL_B2B", "6")),
    "B2C": int(os.getenv("FUNIL_B2C", "5")),
    "RETENCAO": int(os.getenv("FUNIL_RETENCAO", "4")),
}

# Intervalo do polling (em minutos) e janelas de segurança (delta)
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "2"))
# Contatos (leads) usam timestamp completo -> varredura em horas.
DELTA_LOOKBACK_HOURS = int(os.getenv("DELTA_LOOKBACK_HOURS", "48"))
# Clientes usam data (dia) -> varredura em dias (pega cadastros recentes).
DELTA_LOOKBACK_DAYS = int(os.getenv("DELTA_LOOKBACK_DAYS", "7"))
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "100"))

# Arquivo onde guardamos o "watermark" (último timestamp já processado) e a lista
# de ids já importados, SEPARADOS por fonte (lead/contato e cliente). Sem isso o
# sync re-varre a MESMA janela a cada ciclo, batendo na API do IXC e re-gravando
# os mesmos registros no Odoo o dia inteiro.
SYNC_STATE_PATH = os.getenv("SYNC_STATE_PATH", "/app/.ixc_sync_state.json")

# O IXC grava horários no fuso de Brasília. O container do Easypanel roda em UTC,
# então SEM isso o filtro de data fica deslocado e o delta sync nunca casa nada.
TZ_BRASIL = ZoneInfo("America/Sao_Paulo")


# ==========================================
# Modelos Pydantic
# ==========================================
class LeadPayload(BaseModel):
    id_ixc: str
    nome: str
    cnpj_cpf: str
    telefone: str
    email: str


# ==========================================
# Conexão Odoo
# ==========================================
def get_odoo_connection():
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
    uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
    return uid, models


def _marcador_lead(id_lead: str) -> str:
    return f"ID_IXC_LEAD:{id_lead};"


def _marcador_cliente(id_cli: str) -> str:
    return f"ID_IXC_CLI:{id_cli};"


def _marcador_documento(documento: str) -> str:
    """Marca a oportunidade pelo CPF/CNPJ, unificando o mesmo lead/cliente
    (vindo de /contato ou /cliente) em UM único card no funil."""
    return f"DOC:{_somente_digitos(documento)};"


def _somente_digitos(valor) -> str:
    return re.sub(r"\D", "", valor or "")


# ==========================================
# Lógica de negócio: roteamento do funil
# ==========================================
def funil_do_lead(documento: str) -> tuple:
    """Contato (lead) sempre vira oportunidade; funil pelo documento."""
    if len(_somente_digitos(documento)) > 11:
        return FUNIL_ODOO["B2B"], "[B2B]"
    return FUNIL_ODOO["B2C"], "[B2C]"


def roteamento_cliente(tipo_pessoa, documento, cliente_ativo,
                       status_prospeccao, data_cadastro) -> tuple:
    """
    Regras para CLIENTE (mesmo critério original):
    1. PJ (ou documento > 11 dígitos) -> B2B, vira oportunidade.
    2. Prospecto, ou cadastro recente, ou inativo -> B2C, vira oportunidade.
    3. Cliente ativo e antigo -> só Contato.
    """
    hoje = datetime.now(TZ_BRASIL).date()
    cutoff = (hoje - timedelta(days=DELTA_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    eh_recente = bool(data_cadastro) and data_cadastro >= cutoff
    eh_pj = tipo_pessoa == "J" or len(_somente_digitos(documento)) > 11

    if eh_pj:
        return True, FUNIL_ODOO["B2B"], "[B2B]"

    if status_prospeccao == "P" or eh_recente or cliente_ativo != "S":
        return True, FUNIL_ODOO["B2C"], "[B2C]"

    return False, None, "[CONTATO]"


# ==========================================
# Cria/atualiza Contato (res.partner) — dedup por documento/email
# ==========================================
def upsert_partner(models, uid, nome, documento, telefone, email, marcador) -> int:
    document_digits = _somente_digitos(documento)
    domain = (
        ["|", ["vat", "=", document_digits], ["email", "=", email]]
        if email else [["vat", "=", document_digits]]
    )
    existente = models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD, "res.partner", "search", [domain]
    )

    eh_empresa = len(document_digits) > 11
    payload = {
        "name": nome,
        "phone": telefone,
        "email": email,
        "vat": document_digits,
        "company_type": "company" if eh_empresa else "person",
        "is_company": eh_empresa,
        "comment": marcador,
    }

    if existente:
        partner_id = existente[0]
        models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, "res.partner", "write", [[partner_id], payload]
        )
        logger.info(f"Contato atualizado: {nome}")
    else:
        partner_id = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, "res.partner", "create", [payload]
        )
        logger.info(f"Contato criado: {nome}")
    return partner_id


# ==========================================
# Cria/atualiza Oportunidade (crm.lead) — dedup pelo documento (CPF/CNPJ)
# ==========================================
def upsert_lead(models, uid, nome, telefone, email, partner_id, documento,
                data_cadastro, team_id, tag, fonte_ref, legacy_marker=None):
    marcador_doc = _marcador_documento(documento)
    # Busca pelo documento OPCIONALMENTE junta com o marcador legado (da versão
    # antiga, `ID_IXC:{id};`) para NÃO duplicar oportunidades já existentes.
    if legacy_marker:
        domain = [
            "|",
            ["description", "ilike", marcador_doc],
            ["description", "ilike", legacy_marker],
        ]
    else:
        domain = [["description", "ilike", marcador_doc]]

    lead_ids = models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "search", [domain]
    )

    payload = {
        "name": f"{tag} - {nome}",
        "contact_name": nome,
        "partner_id": partner_id,
        "email_from": email,
        "phone": telefone,
        "team_id": team_id,
        "description": (
            f"{marcador_doc} {fonte_ref}\n"
            f"Documento: {documento}\nCadastro: {data_cadastro}"
        ),
    }

    if lead_ids:
        models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "write", [[lead_ids[0]], payload]
        )
        logger.info(f"Oportunidade atualizada ({tag}): {nome}")
    else:
        lead_id = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "create", [payload]
        )
        logger.info(f"Oportunidade criada ID {lead_id} ({tag}): {nome}")


# ==========================================
# Processa um CONTATO (lead) vindo de /webservice/v1/contato
# ==========================================
def processar_lead_contato(lead: dict) -> bool:
    try:
        uid, models = get_odoo_connection()
        if not uid:
            logger.error("Falha ao autenticar no Odoo.")
            return False

        id_lead = str(lead.get("id") or lead.get("id_ixc") or "")
        nome = lead.get("nome") or lead.get("razao") or "Sem Nome"
        documento = lead.get("cnpj_cpf", "")
        telefone = (lead.get("fone_celular") or lead.get("fone_whatsapp")
                    or lead.get("fone_comercial") or lead.get("telefone") or "")
        email = lead.get("email", "")
        data_cadastro = lead.get("data_cadastro", "")

        # Já tem vínculo com cliente real -> não é mais lead puro (não cria card no funil).
        if (lead.get("id_cliente") or "0") not in ("0", ""):
            logger.info(f"Contato IXC {id_lead} já é cliente: mantido só em Contatos.")
            return True

        if not _somente_digitos(documento):
            logger.info(f"Lead IXC {id_lead} ignorado (sem CPF/CNPJ): {nome}")
            return True

        team_id, tag = funil_do_lead(documento)
        marcador_partner = f"{_marcador_lead(id_lead)} {_marcador_cliente(id_lead)}"
        partner_id = upsert_partner(models, uid, nome, documento, telefone, email,
                                    marcador_partner)
        upsert_lead(models, uid, nome, telefone, email, partner_id, documento,
                    data_cadastro, team_id, tag, _marcador_lead(id_lead))
        return True

    except Exception as e:
        logger.error(f"Erro ao processar lead IXC: {str(e)}")
        return False


# ==========================================
# Processa um CLIENTE vindo de /webservice/v1/cliente
# ==========================================
def processar_cliente(cliente: dict) -> bool:
    try:
        uid, models = get_odoo_connection()
        if not uid:
            logger.error("Falha ao autenticar no Odoo.")
            return False

        id_cli = str(cliente.get("id") or "")
        nome = cliente.get("razao") or cliente.get("nome") or "Sem Nome"
        documento = cliente.get("cnpj_cpf", "")
        telefone = cliente.get("telefone_celular") or cliente.get("telefone_comercial") or ""
        email = cliente.get("email", "")
        tipo_pessoa = cliente.get("tipo_pessoa", "F")
        cliente_ativo = cliente.get("ativo", "N")
        status_prospeccao = cliente.get("status_prospeccao", "")
        data_cadastro = cliente.get("data_cadastro", "")

        if not _somente_digitos(documento):
            logger.info(f"Cliente IXC {id_cli} ignorado (sem CPF/CNPJ): {nome}")
            return True

        marcador_partner = _marcador_cliente(id_cli)
        partner_id = upsert_partner(models, uid, nome, documento, telefone, email,
                                    marcador_partner)

        deve_criar, team_id, tag = roteamento_cliente(
            tipo_pessoa, documento, cliente_ativo, status_prospeccao, data_cadastro
        )

        if not deve_criar:
            logger.info(f"Cliente IXC {id_cli} ativo/antigo: mantido só em Contatos.")
            return True

        upsert_lead(models, uid, nome, telefone, email, partner_id, documento,
                    data_cadastro, team_id, tag, _marcador_cliente(id_cli),
                    legacy_marker=f"ID_IXC:{id_cli};")
        return True

    except Exception as e:
        logger.error(f"Erro ao processar cliente IXC: {str(e)}")
        return False


# ==========================================
# Controle de estado entre ciclos (watermark + ids já processados)
# ==========================================
def _carregar_estado(ns: str):
    try:
        with open(SYNC_STATE_PATH, encoding="utf-8") as f:
            estado = json.load(f)
        bloco = estado.get(ns, {})
        return bloco.get("ultima", ""), set(bloco.get("processados", []))
    except Exception:
        return "", set()


def _salvar_estado(ns: str, ultima: str, processados: set):
    try:
        try:
            with open(SYNC_STATE_PATH, encoding="utf-8") as f:
                estado = json.load(f)
        except Exception:
            estado = {}
        estado[ns] = {"ultima": ultima, "processados": sorted(processados)}
        with open(SYNC_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(estado, f)
    except Exception as e:
        logger.warning(f"Não foi possível salvar estado do sync ({ns}): {str(e)}")


# ==========================================
# Varredura genérica e incremental de uma fonte do IXC
# ==========================================
def _sincronizar_fonte(endpoint, tabela, campo_data, ns, processar,
                       data_only, lookback):
    try:
        agora = datetime.now(TZ_BRASIL)
        agora_ref = (agora.date().strftime("%Y-%m-%d") if data_only
                     else agora.strftime("%Y-%m-%d %H:%M:%S"))
        # Se o campo é "dia", sobra um pouco: (now - lookback em dias)
        if data_only:
            wm_inicial = (agora - timedelta(days=lookback)).date().strftime("%Y-%m-%d")
        else:
            wm_inicial = (agora - timedelta(hours=lookback)).strftime("%Y-%m-%d %H:%M:%S")

        watermark, processados = _carregar_estado(ns)
        # Se o campo é "dia" mas veio watermark com hora (estado antigo), reseta.
        if data_only and len(watermark) > 10:
            logger.info(f"{ns}: watermark antigo ({watermark}) inválido para campo de dia; resetando.")
            watermark = ""
        if not watermark:
            watermark = wm_inicial
            logger.info(f"{ns}: backfill inicial desde {watermark} (Brasília).")

        headers = {"ixcsoft": "listar", "Content-Type": "application/json"}
        pagina = 1
        novos = 0
        max_data = watermark

        while True:
            query_payload = {
                "qtype": f"{tabela}.{campo_data}",
                "query": watermark,
                "oper": ">=",
                "page": str(pagina),
                "rp": str(PAGE_SIZE),
                "sortname": f"{tabela}.id",
                "sortorder": "desc",
            }

            try:
                response = requests.post(
                    f"{IXC_HOST}/{endpoint}",
                    json=query_payload,
                    headers=headers,
                    auth=(IXC_USER, IXC_PASS),
                    timeout=15,
                )
            except requests.RequestException as e:
                logger.error(f"Falha de rede na API do IXC ({endpoint}): {str(e)}")
                break

            if response.status_code != 200:
                logger.warning(
                    f"Erro na API do IXC ({endpoint}): Status {response.status_code} "
                    f"{response.text[:300]}"
                )
                break

            registros = response.json().get("registros", [])

            for item in registros:
                id_item = str(item.get("id"))
                if id_item in processados:
                    continue

                if processar(item):
                    processados.add(id_item)
                    ts = str(item.get(campo_data) or "")
                    if ts and not ts.startswith("0000-00-00") and ts > max_data:
                        max_data = ts
                    novos += 1

            if len(registros) < PAGE_SIZE:
                break
            pagina += 1

        # nunca deixa o watermark ir para o futuro além do relógio real (Brasília)
        if max_data > agora_ref:
            max_data = agora_ref

        _salvar_estado(ns, max_data, processados)
        logger.info(f"{ns}: ciclo concluído, {novos} novos importados (watermark {max_data}).")

    except Exception as e:
        logger.error(f"Falha no ciclo de Polling do IXC ({ns}): {str(e)}")


def sync_leads_ixc():
    logger.info("Iniciando varredura de LEADS (/contato)...")
    _sincronizar_fonte(
        endpoint="webservice/v1/contato",
        tabela="contato",
        campo_data="data_cadastro",
        ns="lead",
        processar=processar_lead_contato,
        data_only=False,
        lookback=DELTA_LOOKBACK_HOURS,
    )


def sync_clientes_ixc():
    logger.info("Iniciando varredura de CLIENTES (/cliente)...")
    _sincronizar_fonte(
        endpoint="webservice/v1/cliente",
        tabela="cliente",
        campo_data="data_cadastro",
        ns="cliente",
        processar=processar_cliente,
        data_only=True,
        lookback=DELTA_LOOKBACK_DAYS,
    )


# ==========================================
# Agendador em segundo plano
# ==========================================
scheduler = BackgroundScheduler()
scheduler.add_job(
    sync_leads_ixc,
    "interval",
    minutes=POLL_INTERVAL_MINUTES,
    max_instances=1,
    next_run_time=datetime.now(),
)
scheduler.add_job(
    sync_clientes_ixc,
    "interval",
    minutes=POLL_INTERVAL_MINUTES,
    max_instances=1,
    next_run_time=datetime.now(),
)


@app.on_event("startup")
def start_scheduler():
    scheduler.start()
    logger.info(f"Scheduler iniciado (a cada {POLL_INTERVAL_MINUTES} min).")


@app.on_event("shutdown")
def stop_scheduler():
    scheduler.shutdown()


# ==========================================
# Rota HTTP Webhook (mantida para formulários externos / landing pages)
# ==========================================
@app.post("/webhook/novo-cliente")
async def webhook_novo_cliente(
    payload: LeadPayload,
    background_tasks: BackgroundTasks,
    token: str = Header(None),
):
    if not WEBHOOK_SECRET or token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Token inválido")

    lead = {
        "id_ixc": payload.id_ixc,
        "nome": payload.nome,
        "cnpj_cpf": payload.cnpj_cpf,
        "telefone": payload.telefone,
        "email": payload.email,
    }
    background_tasks.add_task(processar_lead_contato, lead)
    return {"message": "Lead recebido. Processando em segundo plano."}


@app.get("/health")
def health_check():
    return {"status": "ok"}
