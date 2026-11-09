"""IXC <-> Odoo Bridge (MaisFibra).

Rotas:
  GET  /health
  POST /webhook/novo-cliente   (legado; header token = WEBHOOK_SECRET)
  POST /webhook/lead-externo   (OPA/Chatwoot/landing; header token = WEBHOOK_SECRET)
  POST /odoo/prospect          (Odoo -> IXC; header token = BRIDGE_TOKEN)
  POST /odoo/negociacao        (Odoo -> IXC; header token = BRIDGE_TOKEN)
  POST /odoo/cliente-ixc       (Odoo -> IXC, leitura: contratos e faturas; header token = BRIDGE_TOKEN)
  POST /odoo/consulta-credito  (Odoo -> IXC; header token = BRIDGE_TOKEN)

Jobs:
  leads, clientes, status (ganho/perda) a cada POLL_INTERVAL_MINUTES
  condomínios e planos a cada CADASTROS_INTERVAL_MINUTES
"""
import hmac
import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from pydantic import BaseModel

import config
import consulta_ixc
import ixc
import ixc_para_odoo as sync
import odoo_para_ixc as fluxo

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("IXC-Odoo-Bridge")

app = FastAPI(title="IXC <-> Odoo Bridge (MaisFibra)")


def _auth(recebido, esperado):
    if not esperado or not recebido or not hmac.compare_digest(recebido, esperado):
        raise HTTPException(status_code=401, detail="Token inválido")


# ======================================================================
# Modelos
# ======================================================================
class LeadPayload(BaseModel):
    id_ixc: str
    nome: str
    cnpj_cpf: str
    telefone: str
    email: str


class LeadExterno(BaseModel):
    nome: str
    telefone: str = ""
    email: str = ""
    cpf_cnpj: str = ""
    origem: str = ""          # "de onde nos conheceu" (mensagem do OPA/Chatwoot)
    canal_venda: str = ""


class OdooLead(BaseModel):
    """Payload enviado pelo módulo maisfibra_crm (crm.lead._mf_payload)."""
    model_config = {"extra": "allow"}
    odoo_lead_id: int
    nome: str
    cpf_cnpj: str
    email: str = ""
    telefone: str = ""
    ixc_contato_id: str = ""
    ixc_cliente_id: str = ""
    ixc_negociacao_id: str = ""
    cidade: str = ""
    cidade_ixc_id: str = ""
    plano_ixc_id: Optional[str] = ""
    descricao_produto: Optional[str] = ""


# ======================================================================
# Odoo -> IXC
# ======================================================================
def _executar(func, payload, nome):
    try:
        return func(payload.model_dump())
    except ixc.IXCError as e:
        logger.warning(f"{nome} recusado: {e}")
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception(f"{nome} falhou")
        raise HTTPException(status_code=502, detail=f"Falha falando com o IXC: {e}")


@app.post("/odoo/prospect")
def odoo_prospect(payload: OdooLead, token: str = Header(None)):
    _auth(token, config.BRIDGE_TOKEN)
    cliente_id = _executar(fluxo.virar_prospect, payload, "prospect")
    return {"ixc_cliente_id": cliente_id}


@app.post("/odoo/negociacao")
def odoo_negociacao(payload: OdooLead, token: str = Header(None)):
    _auth(token, config.BRIDGE_TOKEN)
    neg_id = _executar(fluxo.criar_negociacao, payload, "negociacao")
    return {"ixc_negociacao_id": neg_id}


class ConsultaCliente(BaseModel):
    ixc_cliente_id: str = ""
    cpf_cnpj: str = ""


@app.post("/odoo/cliente-ixc")
def odoo_cliente_ixc(payload: ConsultaCliente, token: str = Header(None)):
    """Contratos, status do serviço e faturas do cliente (somente leitura, tempo real)."""
    _auth(token, config.BRIDGE_TOKEN)
    try:
        return consulta_ixc.consultar(payload.ixc_cliente_id, payload.cpf_cnpj)
    except ixc.IXCError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("consulta cliente IXC falhou")
        raise HTTPException(status_code=502, detail=f"Falha falando com o IXC: {e}")


@app.post("/odoo/consulta-credito")
def odoo_consulta_credito(payload: OdooLead, token: str = Header(None)):
    _auth(token, config.BRIDGE_TOKEN)
    # TODO(handoff): implementar com a documentação de "Central de consultas (CPF|CNPJ)"
    # que o cliente possui. Retornar {"status": "aprovado"|"reprovado"|"pendente", "mensagem": "..."}.
    raise HTTPException(status_code=501, detail="Consulta de crédito ainda não implementada (aguardando documentação do IXC).")


# ======================================================================
# Entradas externas
# ======================================================================
@app.post("/webhook/lead-externo")
def webhook_lead_externo(payload: LeadExterno, background_tasks: BackgroundTasks,
                         token: str = Header(None)):
    _auth(token, config.WEBHOOK_SECRET)
    background_tasks.add_task(fluxo.lead_externo, payload.model_dump())
    return {"message": "Lead recebido. Criando no IXC e no Odoo."}


@app.post("/webhook/novo-cliente")
def webhook_novo_cliente(payload: LeadPayload, background_tasks: BackgroundTasks,
                         token: str = Header(None)):
    _auth(token, config.WEBHOOK_SECRET)
    background_tasks.add_task(sync.processar_contato, payload.model_dump())
    return {"message": "Lead recebido. Processando em segundo plano."}


@app.get("/health")
def health_check():
    return {"status": "ok", "mf_campos": config.MF_CAMPOS}


# ======================================================================
# Agendador
# ======================================================================
scheduler = BackgroundScheduler(timezone=str(config.TZ_BRASIL))
_agora = datetime.now(config.TZ_BRASIL)
for job in (sync.sync_leads, sync.sync_clientes, sync.sync_status):
    scheduler.add_job(job, "interval", minutes=config.POLL_INTERVAL_MINUTES,
                      max_instances=1, coalesce=True, next_run_time=_agora)
for job in (sync.sync_condominios, sync.sync_planos):
    scheduler.add_job(job, "interval", minutes=config.CADASTROS_INTERVAL_MINUTES,
                      max_instances=1, coalesce=True, next_run_time=_agora)


@app.on_event("startup")
def start_scheduler():
    scheduler.start()
    logger.info(f"Scheduler iniciado (polling {config.POLL_INTERVAL_MINUTES} min, "
                f"cadastros {config.CADASTROS_INTERVAL_MINUTES} min, MF_CAMPOS={config.MF_CAMPOS}).")


@app.on_event("shutdown")
def stop_scheduler():
    scheduler.shutdown()
