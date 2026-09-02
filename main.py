import os
import json
import re
from datetime import datetime, timedelta
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

# Intervalo do polling (em minutos) e janela de segurança (delta)
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "2"))
# Quantas horas olhar para trás em "ultima_atualizacao" (pega mudanças de status
# em clientes criados em dias anteriores, ex: prospecto que virou lead).
DELTA_LOOKBACK_HOURS = int(os.getenv("DELTA_LOOKBACK_HOURS", "48"))
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "100"))

# Arquivo onde guardamos o "watermark" (último timestamp já processado) e a lista
# de id_ixc já importados. Sem isso o sync re-varre a MESMA janela a cada ciclo,
# batendo na API do IXC e re-gravando os mesmos registros no Odoo o dia inteiro.
SYNC_STATE_PATH = os.getenv("SYNC_STATE_PATH", "/app/.ixc_sync_state.json")

# O IXC grava horários no fuso de Brasília. O container do Easypanel roda em
# UTC, então SEM isso o filtro de data fica até 3h "no futuro" em relação
# ao relógio real do IXC, e o delta sync nunca casa nada.
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


def _marcador_ixc(id_ixc: str) -> str:
    """
    Marcador único e delimitado para evitar falso positivo de substring
    (ex: ID_IXC:12; não bate como substring de ID_IXC:112;).
    """
    return f"ID_IXC:{id_ixc};"


# ==========================================
# Lógica de negócio: roteamento
# ==========================================
def decidir_roteamento(tipo_pessoa: str, documento: str, cliente_ativo: str,
                        status_prospeccao: str, data_cadastro: str) -> tuple:
    """
    Retorna (deve_criar_oportunidade: bool, team_id: int|None, tag: str)

    Regras (na ordem):
    1. PJ (ou documento > 11 dígitos) -> sempre B2B, vira oportunidade.
    2. Prospecto ou cadastro recente (hoje) -> B2C, vira oportunidade.
    3. Cliente já ativo e antigo -> só Contatos, NÃO vira oportunidade
       (evita poluir o funil de Retenção com clientes que já são clientes).
    """
    hoje_str = datetime.now(TZ_BRASIL).strftime("%Y-%m-%d")
    eh_cadastro_recente = bool(data_cadastro) and data_cadastro >= hoje_str
    # remove pontuação do CPF/CNPJ antes de contar dígitos (senão CPF com
    # "000.000.000-00" tem 14 chars e é tratado como PJ -> funil errado)
    digitos = re.sub(r"\D", "", documento or "")
    eh_pj = tipo_pessoa == "J" or len(digitos) > 11

    if eh_pj:
        return True, FUNIL_ODOO["B2B"], "[B2B]"

    if status_prospeccao == "P" or eh_cadastro_recente or cliente_ativo != "S":
        return True, FUNIL_ODOO["B2C"], "[B2C]"

    # Cliente ativo e antigo: não cria card no CRM, só mantém Contato atualizado.
    return False, None, "[CONTATO]"


# ==========================================
# Processamento: cria/atualiza Contato + (opcional) Lead
# ==========================================
def processar_e_enviar_para_odoo(payload: dict) -> bool:
    """Retorna True se o registro foi processado (ou intencionalmente ignorado),
    False se falhou — para poder ser reprocessado no próximo ciclo."""
    try:
        uid, models = get_odoo_connection()
        if not uid:
            logger.error("Falha ao autenticar no Odoo.")
            return False

        id_ixc = str(payload.get("id_ixc"))
        nome = payload.get("nome", "Sem Nome")
        documento = (payload.get("cnpj_cpf") or "").strip()
        documento_digits = re.sub(r"\D", "", documento)
        telefone = payload.get("telefone", "")
        email = payload.get("email", "")
        tipo_pessoa = payload.get("tipo_pessoa", "F")
        cliente_ativo = payload.get("ativo", "N")
        status_prospeccao = payload.get("status_prospeccao", "")
        data_cadastro = payload.get("data_cadastro", "")

        if not documento_digits:
            logger.info(f"IXC {id_ixc} ignorado (sem CPF/CNPJ): {nome}")
            return True

        eh_empresa = tipo_pessoa == "J" or len(documento_digits) > 11

        # 1. Contato (res.partner) — sempre cria/atualiza
        domain_partner = (
            ["|", ["vat", "=", documento_digits], ["email", "=", email]]
            if email else [["vat", "=", documento_digits]]
        )
        partner_existente = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, "res.partner", "search", [domain_partner]
        )

        payload_partner = {
            "name": nome,
            "phone": telefone,
            "email": email,
            "vat": documento_digits,
            "company_type": "company" if eh_empresa else "person",
            "is_company": eh_empresa,
            "comment": f"{_marcador_ixc(id_ixc)} Status IXC: "
                       f"{'Ativo' if cliente_ativo == 'S' else 'Prospecto'}",
        }

        if partner_existente:
            partner_id = partner_existente[0]
            models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, "res.partner", "write",
                [[partner_id], payload_partner],
            )
            logger.info(f"Contato atualizado: {nome} (IXC {id_ixc})")
        else:
            partner_id = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, "res.partner", "create", [payload_partner]
            )
            logger.info(f"Contato criado: {nome} (IXC {id_ixc})")

        # 2. Decide se vira Oportunidade (crm.lead) ou fica só em Contatos
        deve_criar_lead, team_id, tag = decidir_roteamento(
            tipo_pessoa, documento, cliente_ativo, status_prospeccao, data_cadastro
        )

        if not deve_criar_lead:
            logger.info(f"IXC {id_ixc} é cliente ativo/antigo: mantido só em Contatos.")
            return True

        marcador = _marcador_ixc(id_ixc)
        lead_ids = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "search",
            [[["description", "ilike", marcador]]],
        )

        payload_lead = {
            "name": f"{tag} - {nome}",
            "contact_name": nome,
            "partner_id": partner_id,
            "email_from": email,
            "phone": telefone,
            "team_id": team_id,
            "description": f"{marcador} Documento: {documento}\nCadastro: {data_cadastro}",
        }

        if lead_ids:
            models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "write",
                [[lead_ids[0]], payload_lead],
            )
            logger.info(f"Lead IXC {id_ixc} atualizado ({tag}).")
        else:
            lead_id = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, "crm.lead", "create", [payload_lead]
            )
            logger.info(f"Lead IXC {id_ixc} criado com ID {lead_id} ({tag}).")

        return True

    except Exception as e:
        logger.error(f"Erro ao processar IXC {payload.get('id_ixc')}: {str(e)}")
        return False


# ==========================================
# Polling Delta Sync (incremental com watermark)
# ==========================================
def _carregar_estado():
    try:
        with open(SYNC_STATE_PATH, encoding="utf-8") as f:
            estado = json.load(f)
        return estado.get("ultima_atualizacao", ""), set(estado.get("processados", []))
    except Exception:
        return "", set()


def _salvar_estado(ultima_atualizacao: str, processados: set):
    try:
        with open(SYNC_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {"ultima_atualizacao": ultima_atualizacao, "processados": sorted(processados)},
                f,
            )
    except Exception as e:
        logger.warning(f"Não foi possível salvar estado do sync: {str(e)}")


def sync_novos_clientes_ixc():
    try:
        logger.info("Iniciando varredura de leads no IXC...")
        headers = {"ixcsoft": "listar", "Content-Type": "application/json"}

        agora = datetime.now(TZ_BRASIL)
        watermark, processados = _carregar_estado()

        # Primeira execução: ainda não temos referência — faz o "backfill" inicial
        # procurando tudo que mudou nas últimas DELTA_LOOKBACK_HOURS.
        if not watermark:
            watermark = (agora - timedelta(hours=DELTA_LOOKBACK_HOURS)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            logger.info(f"Primeira execução: backfill desde {watermark} (Brasília).")

        pagina = 1
        novos = 0
        max_ultima = watermark

        while True:
            query_payload = {
                "qtype": "cliente.ultima_atualizacao",
                "query": watermark,
                "oper": ">=",
                "page": str(pagina),
                "rp": str(PAGE_SIZE),
                "sortname": "cliente.id",
                "sortorder": "desc",
            }

            try:
                response = requests.post(
                    f"{IXC_HOST}/webservice/v1/cliente",
                    json=query_payload,
                    headers=headers,
                    auth=(IXC_USER, IXC_PASS),
                    timeout=15,
                )
            except requests.RequestException as e:
                logger.error(f"Falha de rede na API do IXC: {str(e)}")
                break

            if response.status_code != 200:
                logger.warning(
                    f"Erro na API do IXC: Status {response.status_code} {response.text[:300]}"
                )
                break

            registros = response.json().get("registros", [])

            for item in registros:
                id_ixc = str(item.get("id"))
                # Já foi importado em um ciclo anterior — pula (evita re-gravar).
                if id_ixc in processados:
                    continue

                ok = processar_e_enviar_para_odoo({
                    "id_ixc": id_ixc,
                    "nome": item.get("razao", ""),
                    "cnpj_cpf": item.get("cnpj_cpf", ""),
                    "telefone": item.get("telefone_celular", "")
                    or item.get("telefone_comercial", ""),
                    "email": item.get("email", ""),
                    "tipo_pessoa": item.get("tipo_pessoa", "F"),
                    "ativo": item.get("ativo", "N"),
                    "status_prospeccao": item.get("status_prospeccao", ""),
                    "data_cadastro": item.get("data_cadastro", ""),
                })

                if ok:
                    processados.add(id_ixc)
                    ua = str(item.get("ultima_atualizacao") or "")
                    if ua and ua > max_ultima:
                        max_ultima = ua
                    novos += 1

            # Se veio menos que o tamanho da página, acabou — senão, próxima página
            if len(registros) < PAGE_SIZE:
                break
            pagina += 1

        _salvar_estado(max_ultima, processados)
        logger.info(f"Ciclo concluído: {novos} novos registros importados (watermark {max_ultima}).")

    except Exception as e:
        logger.error(f"Falha no ciclo de Polling do IXC: {str(e)}")


# ==========================================
# Agendador em segundo plano
# ==========================================
scheduler = BackgroundScheduler()
scheduler.add_job(
    sync_novos_clientes_ixc,
    "interval",
    minutes=POLL_INTERVAL_MINUTES,
    max_instances=1,
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

    background_tasks.add_task(processar_e_enviar_para_odoo, payload.dict())
    return {"message": "Lead recebido. Processando em segundo plano."}


@app.get("/health")
def health_check():
    return {"status": "ok"}