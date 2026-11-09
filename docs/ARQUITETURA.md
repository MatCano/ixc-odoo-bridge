# Documentação de Arquitetura Técnica — Mais Fibra (Odoo + IXC)

Versão 1.0 · 09/11/2026 · Entregável da Frente 0 (Anexo I, item "Documentação de arquitetura técnica")

## 1. Visão de componentes

```
┌────────────────────────── Servidor (Easypanel, projeto "maisfibra") ──────────────────────────┐
│                                                                                                │
│   ┌──────────────┐  XML-RPC   ┌───────────────────┐  HTTP (token)   ┌──────────────────────┐   │
│   │ Odoo 19 CE   │◀──────────▶│ ixc-odoo-bridge   │────────────────▶│ IXC Provedor (API    │   │
│   │ odoo:19.0 +  │  HTTP      │ FastAPI + APSched │  REST/JSON      │ webservice/v1)       │   │
│   │ odoo-addons/ │──────────▶ │ (este repositório)│◀────────────────│                      │   │
│   └──────┬───────┘ /odoo/*    └─────────┬─────────┘   polling       └──────────────────────┘   │
│          │                              │ /webhook/lead-externo                                │
│   ┌──────▼───────┐                      ▲                                                      │
│   │ odoo-db      │               OPA / Chatwoot / landing pages                                │
│   │ postgres:13  │                                                                             │
│   └──────────────┘          ┌──────────────────┐ webhook /maisfibra/whatsapp/webhook           │
│                             │ Evolution API    │──────────────────────────────▶ Odoo           │
│                             │ (a implantar)    │◀────── envio (fila + cron) ── Odoo            │
│                             └──────────────────┘                                              │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Componente | Tecnologia | Responsabilidade |
|---|---|---|
| Odoo | Odoo 19 Community (`odoo:19.0`) + módulos `maisfibra_*` | CRM, funis, cadastros, relatórios, WhatsApp, e-mails, auditoria |
| Banco | PostgreSQL 13 | Dados do Odoo |
| Bridge | Python 3.11, FastAPI, APScheduler, requests, XML-RPC | Integração bidirecional Odoo ↔ IXC |
| IXC Provedor | ERP do provedor (externo) | Fonte de verdade de clientes, contratos e financeiro |
| Evolution API | (a implantar) | Envio/recebimento de WhatsApp; trocável pela Meta Cloud API |

### Módulos Odoo (`odoo-addons/`)
| Módulo | Função |
|---|---|
| `maisfibra_crm` | Campos de negócio, endereço de instalação, condomínio, regras de estágio, chamadas ao bridge, consulta IXC, painel/relatórios, e-mails automáticos, migração |
| `maisfibra_whatsapp` | WhatsApp no lead/contato, webhook de entrada, fila de envio, automações e chatbot |
| `maisfibra_auditoria` | Registro imutável de alterações |

## 2. Fonte de verdade por dado

| Dado | Fonte | Observação |
|---|---|---|
| Lead/prospect/cliente | **IXC** | Odoo espelha; vendedor completa dados no Odoo e o bridge grava no IXC ao entrar em *Recolher Documentos* |
| Funil comercial, atividades, conversas | **Odoo** | |
| Contratos, status do serviço, faturas | **IXC** | Odoo só consulta em tempo real (não duplica) |
| Ganho/perda | **IXC** (`crm_data_vencemos`/`crm_data_perdemos`) | Bridge move o lead no Odoo |
| Condomínios, planos de negociação | **IXC** | Espelhados a cada 60 min |

Regra anti-conflito: em registros existentes o bridge **só preenche campos vazios** no Odoo; não sobrescreve
edição do vendedor. Toda escrita do bridge leva `mf_from_bridge=True`, o que impede o Odoo de disparar de
volta para o IXC (evita loop) e marca a origem na auditoria.

## 3. Fluxos

### 3.1 Entrada de lead
```
IXC (contato/cliente novo) ──polling 2 min──▶ bridge ──▶ Odoo: contato + oportunidade (funil B2B/B2C)
OPA/Chatwoot/landing ──POST /webhook/lead-externo──▶ bridge ──▶ IXC (contato) + Odoo (lead com origem)
WhatsApp (número novo) ──Evolution webhook──▶ Odoo: lead [WhatsApp] (+ chatbot, se ligado)
```

### 3.2 Avanço no funil (Odoo → IXC)
```
Vendedor move para estágio com Regra "Recolher Documentos"
  └▶ Odoo valida (e-mail, CPF/CNPJ, condomínio, bloco, apto, CEP, endereço, cidade)
      └▶ POST bridge /odoo/prospect ──▶ IXC: cliente prospect (insert ou PUT) + vincula contato
          ├─ sucesso: grava ID do prospect no lead e registra no chatter
          └─ erro: estágio NÃO muda; vendedor vê a mensagem do IXC

Vendedor move para estágio com Regra "Negociação"
  └▶ Odoo valida (cidade, CPF, e-mail, plano, descrição, já ser prospect)
      └▶ POST bridge /odoo/negociacao ──▶ IXC: negociação + crm_data_negociando
```

### 3.3 Fechamento (IXC → Odoo)
```
IXC marca vencemos/perdemos ──polling (ultima_atualizacao)──▶ bridge
  └▶ Odoo: lead vai para estágio com Regra "Aguardar Assinatura" (ganho) ou "Perdemos"
      └▶ (se configurado) WhatsApp/e-mail do estágio é enviado ao cliente
```

### 3.4 Consulta financeira
```
Botão "Consultar IXC" ──▶ bridge /odoo/cliente-ixc ──▶ IXC: cliente_contrato + fn_areceber (leitura)
  └▶ janela com faturas/contratos + resumo gravado no cadastro (status do serviço, aberto, vencido)
```

## 4. Interfaces

### Bridge
| Rota | Autenticação | Uso |
|---|---|---|
| `GET /health` | — | Monitoramento |
| `POST /odoo/prospect` | header `token` = `BRIDGE_TOKEN` | Odoo → IXC |
| `POST /odoo/negociacao` | idem | Odoo → IXC |
| `POST /odoo/cliente-ixc` | idem | Consulta (leitura) |
| `POST /odoo/consulta-credito` | idem | **Pendente** (501) |
| `POST /webhook/lead-externo` | header `token` = `WEBHOOK_SECRET` | OPA/Chatwoot/landing |
| `POST /webhook/novo-cliente` | idem | Legado |

### Odoo
| Rota | Autenticação | Uso |
|---|---|---|
| `POST /maisfibra/whatsapp/webhook?token=…` | token em Definições > WhatsApp | Evolution → Odoo |

### IXC (confirmado)
`POST /webservice/v1/<tabela>` com header `ixcsoft: listar` (listar) · `POST` (inserir, retorna `id`) ·
`PUT /<tabela>/<id>` com registro completo (alterar). Tabelas: `cliente`, `contato`, `cidade`,
`cliente_contrato`, `fn_areceber`. A validar: condomínio, negociação, plano.

## 5. Segurança

- Credenciais apenas em variáveis de ambiente do Easypanel e em parâmetros do Odoo — **nunca no Git**.
- Tokens comparados em tempo constante (`hmac.compare_digest`).
- Bridge exposto preferencialmente só na rede interna do Easypanel (Odoo chama pelo nome do serviço).
- HTTPS nos domínios públicos (proxy do Easypanel).
- Auditoria de alterações em leads, contatos, faturas e estágios (`maisfibra_auditoria`).
- **Pendências**: repositório está público (tornar privado + chave SSH no Easypanel); backup externo; VPN para
  acesso administrativo (previsto no contrato).

## 6. Operação

| Item | Como |
|---|---|
| Deploy do bridge | Push na `main` → Easypanel (fonte Git) |
| Deploy dos módulos | Easypanel > odoo > Implantar (fonte Git, `odoo-addons/Dockerfile`) → Apps > Atualizar o módulo |
| Logs | Easypanel > serviço > Logs (bridge loga cada ciclo e cada recusa do IXC com a mensagem original) |
| Estado do sync | `/app/data/ixc_sync_state.json` (volume `bridge-data`) |
| Backup | Diário às 2h, volumes `odoo-db-data` e `odoo-web-data` → disco local (`/backups/maisfibra/…`) |
| CI | GitHub Actions: testes do bridge + checagem dos módulos a cada push |
| Configuração | Variáveis do bridge: ver `README.md`. Odoo: Definições > MaisFibra / WhatsApp |

## 7. Decisões de arquitetura

| Decisão | Motivo |
|---|---|
| Middleware separado (bridge) em vez de lógica IXC dentro do Odoo | Credenciais e conhecimento do IXC num só lugar; deploy independente; Odoo segue atualizável |
| Polling em vez de webhooks do IXC | IXC não oferece webhooks confiáveis para essas tabelas; watermark + janela de segurança |
| IXC continua fonte do financeiro | Evitar duplicidade e divergência; Odoo mostra em tempo real |
| Regras por **código** no estágio (não por nome) | Renomear estágio não quebra integração; cada funil tem seus estágios |
| Automações desligadas por padrão | Evitar disparo em massa para a base histórica no primeiro deploy |
| Camada de provedor no WhatsApp | Trocar Evolution → Meta sem reescrever o módulo |
