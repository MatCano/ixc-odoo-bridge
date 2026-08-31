import os
import xmlrpc.client
from typing import Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks, Header
from pydantic import BaseModel

app = FastAPI(
    title="IXC to Odoo Bridge",
    description="Middleware de integração entre IXC Soft e Odoo CRM",
    version="1.0.0"
)

# Configurações do Odoo via Variáveis de Ambiente
ODOO_URL = os.getenv("ODOO_URL", "http://localhost:8069")
ODOO_DB = os.getenv("ODOO_DB", "maisfibra-crm")
ODOO_USER = os.getenv("ODOO_USER", "admin")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "admin")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "seu_token_seguro")


def get_odoo_client():
    """Realiza a conexão XML-RPC com a API do Odoo."""
    try:
        common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
        if not uid:
            raise Exception("Falha na autenticação com o Odoo. Verifique as credenciais.")
        models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
        return uid, models
    except Exception as e:
        print(f"[ERRO DE CONEXÃO ODOO] {str(e)}")
        raise e


# Modelos dos Dados de Entrada (Payloads)
class IXCClientPayload(BaseModel):
    id_ixc: str
    nome: str
    cpf_cnpj: Optional[str] = None
    telefone: Optional[str] = None
    email: Optional[str] = None


class IXCContractPayload(BaseModel):
    id_ixc: str
    status_contrato: str  # Ex: 'A' para Ativo


# Funções de Processamento em Background
def process_new_lead_in_odoo(payload: IXCClientPayload):
    """Busca/Cria o Contato e gera a Oportunidade no CRM do Odoo."""
    try:
        uid, models = get_odoo_client()

        # 1. Montar busca no Odoo (Prefix Notation para o Odoo: '|' antes das condições)
        if payload.cpf_cnpj:
            domain = ['|', ('ref', '=', payload.id_ixc), ('vat', '=', payload.cpf_cnpj)]
        else:
            domain = [('ref', '=', payload.id_ixc)]

        partner_ids = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'res.partner', 'search',
            [domain]
        )

        if partner_ids:
            partner_id = partner_ids[0]
            # Atualiza os dados de contato existente
            models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'res.partner', 'write',
                [[partner_id], {
                    'phone': payload.telefone,
                    'email': payload.email,
                    'ref': payload.id_ixc
                }]
            )
            print(f"[ODOO] Contato existente atualizado (ID: {partner_id})")
        else:
            # Cria novo Contato
            partner_id = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'res.partner', 'create',
                [{
                    'name': payload.nome,
                    'ref': payload.id_ixc,
                    'vat': payload.cpf_cnpj,
                    'phone': payload.telefone,
                    'email': payload.email,
                    'is_company': False,
                }]
            )
            print(f"[ODOO] Novo contato criado (ID: {partner_id})")

        # 2. Criar a Oportunidade no CRM
        lead_id = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'crm.lead', 'create',
            [{
                'name': f"[IXC Novo Lead] - {payload.nome}",
                'partner_id': partner_id,
                'contact_name': payload.nome,
                'phone': payload.telefone,
                'email_from': payload.email,
                'description': f"Cliente cadastrado via IXC Soft (ID IXC: {payload.id_ixc})",
            }]
        )
        print(f"[ODOO] Oportunidade criada no CRM (ID: {lead_id})")

    except Exception as e:
        print(f"[ERRO PROCESSAMENTO LEAD] {str(e)}")


def process_active_contract_in_odoo(payload: IXCContractPayload):
    """Marca o Contato como Ativo e conclui as Oportunidades como 'Ganho'."""
    try:
        uid, models = get_odoo_client()

        partner_ids = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'res.partner', 'search',
            [[('ref', '=', payload.id_ixc)]]
        )

        if not partner_ids:
            print(f"[ODOO] Contato ref '{payload.id_ixc}' não encontrado para ativação.")
            return

        partner_id = partner_ids[0]

        # 1. Atualizar nota/campo do Contato
        models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'res.partner', 'write',
            [[partner_id], {
                'comment': f"Cliente com contrato ATIVO no IXC (Status: {payload.status_contrato})"
            }]
        )

        # 2. Fechar Oportunidades abertas como Ganho (Won)
        lead_ids = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'crm.lead', 'search',
            [[('partner_id', '=', partner_id), ('active', '=', True)]]
        )

        if lead_ids:
            models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'crm.lead', 'action_set_won',
                [lead_ids]
            )
            print(f"[ODOO] Oportunidade(s) {lead_ids} marcada(s) como GANHO.")

    except Exception as e:
        print(f"[ERRO PROCESSAMENTO ATIVAÇÃO] {str(e)}")


# Endpoints HTTP
@app.get("/health")
def health_check():
    """Endpoint de checagem do Easypanel."""
    return {"status": "online", "service": "ixc-odoo-bridge"}


@app.post("/webhook/novo-cliente")
async def webhook_novo_cliente(
    payload: IXCClientPayload,
    background_tasks: BackgroundTasks,
    token: Optional[str] = Header(None)
):
    """Webhook: Cadastra Contato + Lead no CRM."""
    if WEBHOOK_SECRET and token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Token de segurança inválido")

    background_tasks.add_task(process_new_lead_in_odoo, payload)
    return {"message": "Lead recebido. Processando em segundo plano."}


@app.post("/webhook/contrato-ativo")
async def webhook_contrato_ativo(
    payload: IXCContractPayload,
    background_tasks: BackgroundTasks,
    token: Optional[str] = Header(None)
):
    """Webhook: Atualiza status do contrato para Ativo/Ganho."""
    if WEBHOOK_SECRET and token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Token de segurança inválido")

    if payload.status_contrato.upper() in ["A", "ATIVO"]:
        background_tasks.add_task(process_active_contract_in_odoo, payload)

    return {"message": "Atualização de contrato recebida."}
