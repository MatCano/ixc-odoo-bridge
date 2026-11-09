# ixc-odoo-bridge — integração bidirecional IXC ↔ Odoo (MaisFibra)

Serviço FastAPI que roda no Easypanel ao lado do Odoo. Trabalha junto com o módulo
Odoo **`maisfibra_crm`** (obrigatório para o fluxo novo) e, opcionalmente, `maisfibra_whatsapp`.

## Fluxo implementado

| Evento | Quem dispara | O que acontece |
|---|---|---|
| Lead/cliente novo no IXC | polling (2 min) | Contato + oportunidade no Odoo, com CPF, endereço, condomínio, bloco, apto |
| Lead de OPA/Chatwoot/landing | `POST /webhook/lead-externo` | Cria contato no IXC **e** lead no Odoo, com origem |
| Lead → **Recolher Documentos** | Odoo chama `/odoo/prospect` | Valida e-mail, CPF, condomínio, bloco, apto, CEP, endereço, cidade → salva **prospect** no IXC e vincula ao lead do IXC |
| Lead → **Negociação** | Odoo chama `/odoo/negociacao` | Valida cidade, CPF, e-mail, plano, descrição → cria **negociação** no IXC |
| Ganho no IXC (`crm_data_vencemos`) | polling | Lead vai para **Aguardar Assinatura** |
| Perda no IXC (`crm_data_perdemos`) | polling | Lead vai para **Perdemos** |
| Condomínios e planos | polling (60 min) | Espelhados no Odoo para seleção |
| Consulta de crédito | botão no lead | ⚠️ Rota pronta, **lógica pendente** (falta a doc da Central de Consultas) |

Se o IXC recusar algo, o Odoo **não muda o estágio** e mostra a mensagem do IXC ao vendedor.

## Arquivos
- `config.py` — todas as variáveis de ambiente
- `ixc.py` — cliente da API do IXC (listar / inserir / alterar)
- `odoo.py` — cliente XML-RPC do Odoo
- `ixc_para_odoo.py` — polling IXC → Odoo
- `odoo_para_ixc.py` — ações Odoo → IXC
- `main.py` — rotas e agendador

## Deploy (ordem importa)
1. **Instalar `maisfibra_crm` no Odoo primeiro** (o bridge grava campos `mf_*`).
   Para subir o bridge antes do módulo, use `MF_CAMPOS=0` (modo antigo).
2. Easypanel > `ixc-odoo-bridge` > **Montagens**: volume em `/app/data`
   (sem isso o estado do sync se perde a cada deploy).
3. Easypanel > **Ambiente**: configure as variáveis abaixo e faça o deploy.
4. Odoo > Definições > **MaisFibra**: URL do bridge (interno, ex. `http://ixc-odoo-bridge:8000`)
   e o mesmo `BRIDGE_TOKEN`.
5. Em cada funil, abra os estágios existentes e marque a **Regra MaisFibra**
   (o módulo não cria estágios: os funis PF e PJ já têm "Recolher Documentos").

### Variáveis de ambiente
| Variável | Exemplo | Obs. |
|---|---|---|
| `ODOO_URL` / `ODOO_DB` / `ODOO_USER` / `ODOO_PASSWORD` | `http://odoo:8069` / `maisfibra-crm` | já existiam |
| `IXC_HOST` / `IXC_USER` / `IXC_PASS` | | já existiam |
| `WEBHOOK_SECRET` | token forte | OPA/Chatwoot/formulários |
| `BRIDGE_TOKEN` | outro token forte | Odoo → bridge |
| `MF_CAMPOS` | `1` | `0` = modo antigo |
| `SYNC_STATE_PATH` | `/app/data/ixc_sync_state.json` | precisa do volume |
| `ALERTAS` / `ALERTA_CANAL` / `ALERTA_INTERVALO_MIN` | `1` / `Integração IXC` / `60` | alertas no Odoo Discuss |
| `IXC_TB_CONDOMINIO` / `IXC_TB_NEGOCIACAO` / `IXC_TB_PLANO` | | **VALIDAR** |
| `IXC_CLIENTE_DEFAULTS` | JSON | **VALIDAR** |
| `IXC_PROSPECT_FIELDS` | `{"crm":"S","status_prospeccao":"P","ativo":"N"}` | **VALIDAR** |
| `IXC_NEGOCIACAO_DEFAULTS` | JSON | **VALIDAR** |

## Validação no IXC (fazer uma vez, ~30 min)
Confirmado pela API: rotas `cliente`, `contato`, `cidade`; inserir = POST, alterar = PUT com registro
completo; prospect é o próprio `cliente` com `crm`, `status_prospeccao`, `id_condominio`, `bloco`,
`apartamento`, `crm_data_negociando/vencemos/perdemos`.

A confirmar no IXC da MaisFibra (Configurações > Webservice / wiki oficial):
1. **Nome das tabelas** de Condomínios, Negociações e Planos de negociação → `IXC_TB_*`.
2. **Campos da negociação** (`odoo_para_ixc.criar_negociacao`): hoje envia `id_cliente`, `id_plano`,
   `descricao`, `obs`, `id_lead`, `valor`, `data`. Ajustar nomes se diferentes.
3. **Código de prospect**: abra um prospect existente pela API e veja `status_prospeccao` e `ativo`.
4. **Defaults de cliente**: copie `filial_id`, `id_tipo_cliente`, `tipo_assinante`, `tipo_cliente_scm`,
   `cond_pagamento` de um prospect real para `IXC_CLIENTE_DEFAULTS`.
5. **Campo nome** em condomínio (`condominio`/`nome`?) e plano (`descricao`/`nome`?).
6. Permissões do usuário da API para inserir/alterar em `cliente`, `contato` e negociações.

Os logs do serviço mostram a mensagem exata do IXC em caso de recusa.

## Pendências
- [ ] Consulta de crédito (`/odoo/consulta-credito`) — aguardando documentação do cliente.
- [ ] Mapear `indicado_por`, `id_concorrente`, `id_vendedor` para IDs do IXC (hoje vão em `obs`).
- [ ] OPA/Chatwoot: configurar a automação deles para chamar `/webhook/lead-externo`
      (`{"nome","telefone","email","cpf_cnpj","origem","canal_venda"}`, header `token`).
- [x] Alertas: falhas do sincronismo e das chamadas Odoo→IXC são postadas no canal **Integração IXC**
      do Odoo (Discuss), uma vez por hora no máximo, e de novo quando normaliza. `GET /health` mostra o estado.
