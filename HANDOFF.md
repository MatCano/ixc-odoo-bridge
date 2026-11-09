# Handoff — Projeto Odoo + IXC (Mais Fibra)

**Projeto:** Implantação Odoo para Mais Fibra — CRM com Integração IXC (MAISFIBRA-ODOO-2026)
**Contratante:** Allnnect · **Cliente final:** Mais Fibra
**Data do handoff:** 09/11/2026 · **Branch com todo o trabalho:** `integracao-bidirecional`

---

## 0. Situação em 09/11/2026 (01h30) — comece por aqui

### Já está em produção
| Item | Situação |
|---|---|
| Backup diário (banco + arquivos), 2h | ✅ Rodando (disco local) — falta storage externo |
| Odoo com fonte GitHub (`integracao-bidirecional`, `/odoo-addons`) | ✅ Implantado |
| Gerenciador de bancos público | ✅ Desligado (`--no-database-list`) |
| Módulos `maisfibra_crm`, `_whatsapp`, `_auditoria`, `_csat`, `_financeiro` | ✅ Instalados e conferidos. **Nenhuma regra de estágio marcada** e **automações desligadas** — nada é enviado ao IXC, WhatsApp ou e-mail ainda |
| Volume `/app/data` no bridge | ✅ Criado |
| Validação do IXC (leitura) | ✅ Feita — §6 |

### Seu roteiro (nesta ordem)
| # | Passo | Tempo | Referência |
|---|---|---|---|
| 1 | Odoo > Definições > MaisFibra > **Migrar agora** (se ainda não rodou) | 1 min | §8 |
| 1b | Easypanel > odoo > **Implantar** e, no Odoo, Apps > **Atualizar** `maisfibra_crm` e `maisfibra_whatsapp` (correções da revisão de 09/11: lead duplicado por mensagem de número desconhecido) | 5 min | commit "Revisão" |
| 2 | Conferir na tela do IXC: plano de negociação 33, funil 7, IDs dos canais, permissão de escrita da API | 15 min | §6 |
| 3 | **Bridge novo**: variáveis no Easypanel (`BRIDGE_TOKEN`, `MF_CAMPOS=1`, `SYNC_STATE_PATH`) → merge do **PR #2** → URL/token em Definições > MaisFibra | 15 min | PR #2 |
| 4 | Testar **Consultar IXC** num cliente; ver o canal "Integração IXC" | 10 min | `docs/PLANO_DE_TESTES.md` S8 |
| 5 | Marcar a **Regra MaisFibra** nos estágios de PF e PJ; teste ponta a ponta com 1 lead real | 1 h | §4, PLANO S1–S7 |
| 6 | Segurança: restringir painel do Easypanel pelo IP, regerar tokens de deploy, repo privado (com chave SSH), 2FA | 1h30 | relatório de segurança (arquivo à parte) |
| 7 | Pedir conta de SMTP e subir Evolution (WhatsApp); levar as decisões do §7 | — | §7 |
| 8 | Executar o plano de testes e montar o relatório | sem. 2–3 | `docs/PLANO_DE_TESTES.md` |

Backlog completo com prazos até 30/11: `docs/backlog_jira_clickup.csv` (importar no Jira/ClickUp).
Guia para a equipe: `docs/GUIA_DO_USUARIO.md`.

Documentos: [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md) · [`docs/PLANO_DE_TESTES.md`](docs/PLANO_DE_TESTES.md) ·
[`docs/OPERACAO.md`](docs/OPERACAO.md) · [`docs/GOVERNANCA_E_LGPD.md`](docs/GOVERNANCA_E_LGPD.md) (rascunho para assinatura).

⚠️ Dado importante: dos ~3.000 leads, só 2 estão como **ganhos** no Odoo (as vendas fecham no IXC). As taxas de
conversão do painel só ficam corretas depois que o bridge novo passar a ler o status das negociações (V/D).

**Progresso estimado:** ~75% desenvolvido · ~40% em produção · prazo-alvo 30/11 (ver §7 sobre o que cabe).

---

## 1. Visão geral

```
                ┌──────────────┐  polling 2 min (leads, clientes, ganho/perda)
   IXC Soft ◀──▶│ ixc-odoo-    │  polling 60 min (condomínios, planos)
  (ERP do       │ bridge       │──────────────────────────────▶ Odoo 19 Community
   provedor)    │ FastAPI      │◀──── /odoo/* (prospect, negociação, consulta) ──
                └──────▲───────┘
                       │ /webhook/lead-externo
            OPA / Chatwoot / landing pages

   Evolution API ◀────────────── maisfibra_whatsapp (webhook /maisfibra/whatsapp/webhook)
```

| Peça | Onde | Observação |
|---|---|---|
| Odoo **19.0 Community** | Easypanel `maisfibra/odoo` — imagem `odoo:19.0` | URL: `https://maisfibra-odoo.ys2bw5.easypanel.host/odoo` · banco `maisfibra-crm` · volumes `/var/lib/odoo`, `/etc/odoo`, `/mnt/extra-addons` |
| PostgreSQL | Easypanel `maisfibra/odoo-db` — imagem `postgres:13` como **app comum** | Volume `/var/lib/postgresql/data/pgdata` |
| Bridge | Easypanel `maisfibra/ixc-odoo-bridge` — fonte GitHub (deploy a cada commit) | Este repositório (raiz) · **sem volume** |
| Painel | `https://easypanel.allnnect.com.br` (servidor 147.79.106.154) | |
| Módulos Odoo | `odoo-addons/` deste repositório | `maisfibra_crm`, `maisfibra_whatsapp` |
| Evolution API | **ainda não existe** | Subir pelo template do Easypanel |

⚠️ O Odoo é **19 Community**, não 18 nem Enterprise. Isso importa para o escopo (ver §5).

### 🔴 Riscos de infraestrutura encontrados (09/11/2026)
1. **Backup** — ✅ *resolvido parcialmente em 09/11*: backup de volume diário às 2h configurado para
   `odoo-db` (`/backups/maisfibra/odoo-db`) e `odoo` (`/backups/maisfibra/odoo-filestore`), primeiro backup
   executado. ⚠️ O destino é **Local Disk (mesmo servidor)**: protege contra erro/deploy quebrado, não contra
   perda do servidor. **Falta**: cadastrar storage externo (S3/B2) no Easypanel, apontar os backups para ele
   e testar uma restauração. Backup de volume de Postgres em execução pode ficar inconsistente — o ideal é
   somar um `pg_dump` diário (ou o backup do gerenciador de bancos do Odoo).
2. **Bridge sem volume** — ✅ *resolvido em 09/11*: volume `bridge-data` em `/app/data` (vale a partir do
   próximo deploy).
3. A URL do gatilho de deploy do bridge contém token — não compartilhar fora da equipe.

---

## 2. Estado atual do Odoo (levantado em 08/11/2026)

- Apps: CRM, Vendas, Contatos, Faturamento (account + l10n_br), Site, Calendário, Pesquisa.
- 3 usuários internos; ~703 leads; ~5.575 contatos.
- Funis: **Cliente PF**, **Cliente PJ**, **Renovação**, **Website**, com estágios próprios
  (incluindo "Recolher Documentos" em PF e PJ).
- Campos criados à mão (Studio-like): `x_ID_IXC`, `x_id_IXC`, `x_status_ixc`, `x_viabilidade` (lead),
  `x_codigo_cliente_ixc` (contato). **Duplicados/legados** — migrar para os campos `mf_*` e arquivar.
- 1 automação: "Alerta de Lead Sem Atendimento (4h)".
- **Nenhum servidor de e-mail de saída** configurado. Nenhum template de e-mail customizado.
- Financeiro: plano de contas BR padrão carregado, diários padrão, 8 faturas manuais, 0 centros de custo.

---

## 3. O que está pronto

### Bridge (`/`)
| Função | Arquivo |
|---|---|
| IXC → Odoo: leads (`contato`) e clientes viram contato + oportunidade, com CPF, endereço, condomínio, bloco, apto | `ixc_para_odoo.py` |
| Odoo → IXC: **Recolher Documentos** salva o lead como **prospect** no IXC | `odoo_para_ixc.py` |
| Odoo → IXC: **Negociação** cria a negociação no IXC | `odoo_para_ixc.py` |
| IXC → Odoo: ganho (`crm_data_vencemos`) → Aguardar Assinatura; perda (`crm_data_perdemos`) → Perdemos | `ixc_para_odoo.py` |
| Sync de condomínios e planos de negociação | `ixc_para_odoo.py` |
| OPA/Chatwoot/landing → cria no IXC **e** no Odoo, com origem | `odoo_para_ixc.py`, rota `/webhook/lead-externo` |
| Consulta em tempo real de **contratos, status do serviço e faturas** | `consulta_ixc.py`, rota `/odoo/cliente-ixc` |
| Consulta de crédito | rota pronta, **lógica pendente** |

Correções em relação à versão em produção: marcador `ID_IXC_CLI` aplicado por engano a leads;
estado do sync agora em volume e com limite; vínculo IXC↔Odoo por campos em vez de texto na descrição.

### `maisfibra_crm`
- Endereço de instalação (duplicado) em contato e lead; condomínio, bloco, apto.
- CPF/CNPJ, origem ("como nos conheceu"), canal de venda, indicado por, concorrente.
- **Regra MaisFibra** no estágio (Recolher Documentos / Negociação / Aguardar Assinatura / Perdemos)
  com validação de campos obrigatórios e chamada ao IXC. Se o IXC recusar, o lead **não muda** de estágio.
- Aba Negociação (plano + descrição do produto + ID IXC).
- Botão **Consultar IXC** (lead e contato): faturas, contratos, status do serviço; resumo gravado no cadastro.
- **Relatórios** (CRM > Relatórios > MaisFibra): Painel de Vendas, Desempenho de Vendedores,
  Leads por Origem, Análise de Leads (promissores / em risco), Relatório de Vendedores em **PDF**.

### `maisfibra_whatsapp`
- Botão WhatsApp no lead/contato; histórico no chatter; mensagem de número novo cria lead (origem WhatsApp).
- Provedor Evolution; camada pronta para trocar pela Meta.

### Testes
13 cenários do fluxo do bridge com IXC/Odoo simulados — todos passando. **Nada testado contra IXC/Odoo reais.**

---

## 4. Como implantar (ordem importa)

1. **Validar o IXC** (§6) — ~30 min com acesso à API.
2. **Backup antes de tudo** (ver riscos acima) e, se possível, testar a instalação numa cópia do banco.
3. **Módulos no Odoo**: trocar a fonte do serviço `odoo` no Easypanel de *Imagem Docker `odoo:19.0`*
   para **Github → `MatCano/ixc-odoo-bridge`, branch `integracao-bidirecional` (depois `main`), caminho `odoo-addons`** (há um `Dockerfile` pronto que
   parte do `odoo:19.0` e acrescenta os módulos em `/mnt/maisfibra-addons`). Manter as 3 montagens.
   Implantar, depois *Apps > Atualizar lista* e instalar `maisfibra_crm`
   (e `maisfibra_whatsapp` quando a Evolution existir).
4. **Estágios**: CRM > Configuração > Estágios — marcar a **Regra MaisFibra** nos estágios existentes
   de PF e PJ (Recolher Documentos, e criar/marcar Negociação, Aguardar Assinatura, Perdemos).
5. **Bridge no Easypanel**:
   - Montagem: volume em `/app/data`.
   - Ambiente: variáveis do `README.md` (novas: `BRIDGE_TOKEN`, `MF_CAMPOS`, `IXC_TB_*`, `IXC_*_DEFAULTS`).
   - Se o deploy do bridge vier **antes** do módulo: `MF_CAMPOS=0`.
6. **Merge** de `integracao-bidirecional` → `main` (dispara o redeploy do bridge).
7. Odoo > Definições > **MaisFibra**: URL interna do bridge + `BRIDGE_TOKEN`.
8. Definições > MaisFibra > **Migrar agora** (vínculos antigos → campos novos).
9. Teste ponta a ponta com 1 lead real: Recolher Documentos → Negociação → marcar ganho no IXC.
10. **WhatsApp** (opcional nesta etapa): subir Evolution, escanear QR, configurar em Definições > WhatsApp.

---

## 5. Contrato × situação (Anexo I)

Legenda: ✅ feito · 🟡 código pronto, falta implantar/validar · ⚠️ parcial · ❌ não feito

### Frente 0 — Governança e infraestrutura
| Item | Status | Nota |
|---|---|---|
| PostgreSQL | ✅ | `odoo-db` |
| Ambientes Dev / Homolog / Prod | 🟡 | Roteiro pronto em `docs/OPERACAO.md` §3 (homologação com cópia do banco); falta executar |
| Backup diário, 30 dias | ⚠️ | Diário às 2h no disco local; roteiro de storage externo, pg_dump e teste de restauração em `docs/OPERACAO.md` |
| Auditoria de alterações | 🟡 | `maisfibra_auditoria` pronto — falta instalar |
| Política de governança / LGPD (assinada) | 🟡 | `docs/GOVERNANCA_E_LGPD.md` — falta DPO, revisão e assinatura |
| Doc. de arquitetura | ✅ | `docs/ARQUITETURA.md` |
| Relatório de segurança e conformidade | ✅ | Entregue **fora do repositório** (lista vulnerabilidades em aberto). Incluir aqui só depois de o repo ficar privado e os itens críticos corrigidos |
| Endurecimento do Odoo | 🟢 | Gerenciador de bancos público desligado + proxy mode no `odoo-addons/Dockerfile` (vale no próximo deploy) |
| Gestão do projeto (Jira/ClickUp) | 🟡 | Backlog com 27 itens, sprints e prazos até 30/11 em `docs/backlog_jira_clickup.csv` — importar no Jira ou ClickUp |
| CI/CD | ⚠️ | CI (GitHub Actions) a cada push; deploy pelo Easypanel |
| VPN para acesso administrativo | ❌ | Roteiro em `docs/OPERACAO.md` §4 |

### Frente 1 — CRM + IXC
| Item | Status | Nota |
|---|---|---|
| Odoo instalado, usuários | ✅ | |
| Funil personalizado | ✅ | PF, PJ, Renovação, Website |
| Campos customizados + validações | 🟡 | `maisfibra_crm` |
| Middleware bidirecional (clientes) | 🟡 | Bridge v2 |
| Alertas automáticos de falha (proposta comercial) | 🟡 | Canal "Integração IXC" no Odoo Discuss |
| Dados financeiros em tempo real | 🟡 | Botão Consultar IXC |
| Status de serviços | 🟡 | Idem (contrato + status da internet) |
| Testes com 100+ clientes + relatório | 🟡 | Roteiro e modelo de relatório em `docs/PLANO_DE_TESTES.md` — falta executar |
| Dashboards de vendas | 🟡 | Painel de Vendas |
| Relatório de vendedores PDF/Excel | 🟡 | PDF no módulo; Excel pelo pivot |
| Análise de leads | 🟡 | Filtros promissores/em risco |

### Frente 2 — E-mail e WhatsApp
| Item | Status | Nota |
|---|---|---|
| SMTP dedicado | ❌ | Precisa de conta (SendGrid etc.) |
| E-mails automáticos (boas-vindas, confirmação) | 🟡 | 4 templates + automação por estágio, desligados até existir SMTP |
| E-mails de cobrança | ❌ | Decisão pendente: o IXC já tem régua de cobrança (§7) |
| Integração WhatsApp | 🟡 | `maisfibra_whatsapp` (Evolution) |
| Pesquisa de satisfação (CSAT) | 🟡 | `maisfibra_csat`: link personalizado por WhatsApp/e-mail ao entrar no estágio marcado; nota volta ao lead; nota 1-2 gera atividade para o gerente |
| Disparos automáticos por evento | 🟡 | Boas-vindas a lead novo + mensagem por estágio (desligados por padrão) |
| Chatbot básico + tickets | 🟡 | Menu 1/2/3; "contratar"/"atendente" cria atividade para o vendedor e pausa o bot |
| Testes de entrega | ❌ | |

### Frente 3 — Financeiro
Base pronta no módulo `maisfibra_financeiro` (falta instalar) — só o que **não duplica o IXC**:

| Item | Status | Nota |
|---|---|---|
| Plano de contas | ⚠️ | Plano BR padrão carregado; adaptar com a contabilidade da Mais Fibra (documento a assinar) |
| Centros de custo por CNPJ | 🟡 | Plano analítico "Centros de Custo" criado; ativar *Contabilidade Analítica* e criar um centro por CNPJ |
| Configuração fiscal (ICMS, ISS, PIS, COFINS, IR) | ❌ | Exige contador; no Community o l10n_br é limitado (avaliar OCA `l10n-brazil`) |
| Contas a pagar + aprovação multinível | 🟡 | Gerente ≤ R$5 mil, Diretor ≤ R$20 mil, CFO acima (configurável); bloqueia lançamento sem aprovação; desligado por padrão |
| Agendamento de pagamentos | 🟡 | Agenda (calendário) de vencimentos, próximos 7 dias, atrasados |
| Relatórios: fluxo de caixa, DRE | 🟡 | Fluxo de caixa previsto e DRE gerencial (pivot, exporta Excel). Balanço patrimonial oficial: Enterprise ou OCA |
| Contas a receber + fatura automática ao ganhar | ⏸️ | **Decisão**: duplicaria o IXC |
| Workflow de cobrança | ⏸️ | **Decisão**: o IXC já tem régua de cobrança |
| Conciliação bancária automática | ❌ | Community 19 não tem; OCA `account_reconcile_oca` ou Enterprise |

---

## 6. Validação no IXC — ✅ feita em 09/11/2026 (scripts `scripts/validar_ixc*.py`, só leitura)

| Item | Resultado | Onde está no código |
|---|---|---|
| Tabela de condomínios | `cliente_condominio` (nome em `condominio`, cidade em `id_cidade`) | `config.IXC_TB_CONDOMINIO` |
| Tabela de negociações | `crm_negociacoes` (4.714 registros) | `config.IXC_TB_NEGOCIACAO` |
| Status da negociação | `N` em andamento · `V` ganha · `D` perdida (batem com `data_vencemos`/`data_perdemos`) | `odoo_para_ixc.STATUS_ABERTA`, `processar_status_negociacao` |
| Ganho/perda | Ficam **na negociação**, não no cliente → bridge lê as duas | `sync_status_negociacoes` |
| Prospect | `crm=S`, `status_prospeccao=P`, `ativo=S` | `config.IXC_PROSPECT_FIELDS` |
| Defaults de cliente | filial 1, assinante 3, SCM `01`, pagamento `0`, localidade U | `config.IXC_CLIENTE_DEFAULTS` |
| Planos | `id_plano` → `vd_contratos` (116 planos de serviço) | `config.IXC_TB_PLANO` |
| Funil da negociação | `crm_funil`: CLIENTE PF, CLIENTE PJ, RENOVAÇÃO, Novos leads; 97% usam o **7** | `config.IXC_NEGOCIACAO_DEFAULTS` |
| Endereço da negociação | 98% usam o do cliente (`considera_endereco_cliente=S`) | idem |
| Canal de venda | `id_canal_origem` (cadastrar o ID do IXC em CRM > Configuração > Canais de venda) | `canal_venda_ixc_id` |

**Ainda conferir na tela do IXC (5 min):**
1. Qual é o **plano de negociação 33** (CRM > Cadastros > Planos de negociações) — é o padrão usado
   (`id_plano_negocio`), porque a tabela não é exposta pela API.
2. Qual funil corresponde ao **id 7** e se leads PJ devem ir para outro funil.
3. IDs dos **canais de venda** do IXC para preencher no Odoo.
4. Permissão do usuário da API para **inserir/alterar** em `cliente`, `contato` e `crm_negociacoes`
   (a validação só testou leitura).

## 7. Decisões pendentes (levar para Allnnect / cliente)

1. **Community × Enterprise.** O contrato descreve recursos que no Odoo são Enterprise
   (conciliação bancária, DRE/Balanço, aprovações multinível, Helpdesk/chatbot com tickets, Studio).
   No Community isso exige módulos OCA ou desenvolvimento — a **Frente 3 fica bem maior** que o cronograma.
2. **Duplicidade com o IXC.** O IXC já é o financeiro do provedor e já tem régua de cobrança
   (campos `regua_cobranca_wpp`/`regua_cobranca_notificacao`). Decidir se o Odoo deve **refazer**
   cobrança/contas a receber ou apenas **mostrar** o que está no IXC (o que já foi implementado).
3. **Infraestrutura**: contrato fala em cloud + 3 ambientes + VPN; realidade é um Easypanel. Registrar
   como acordado ou como pendência.
4. **WhatsApp**: Evolution (rápido, risco de banimento em disparo em massa) × Meta oficial
   (necessária para régua/CSAT em volume).
5. **Origem do lead**: OPA continua como entrada até o WhatsApp no Odoo estabilizar?

---

## 8. Pendências técnicas (próximos passos sugeridos)

1. Validar IXC (§6) e implantar (§4).
2. Após instalar o módulo: **Definições > MaisFibra > Migrar agora** (converte os vínculos que o bridge antigo
   gravava como texto em ~3.000 leads para os campos `mf_*`). Os campos `x_ID_IXC`, `x_id_IXC`,
   `x_status_ixc`, `x_viabilidade` e `x_codigo_cliente_ixc` estão **vazios** — podem ser removidos.
3. Consulta de crédito (aguarda doc da "Central de consultas" do cliente).
4. Mapear `indicado_por`, `id_concorrente`, `id_vendedor` para IDs do IXC (hoje vão em `obs`).
5. WhatsApp: escrever as mensagens nos estágios e ligar automações/chatbot depois de testar com 1 número.
6. SMTP (SendGrid etc.) → ligar os e-mails automáticos e escolher o template em cada estágio.
7. Instalar `maisfibra_auditoria`; storage externo para backup + teste de restauração.
8. Tornar o repositório **privado** (hoje é público) e cadastrar chave SSH no Easypanel.
8. Teste com 100+ clientes e relatório de testes (exigido no contrato).

---

## 9. Acessos a repassar (não versionar segredos)

- GitHub `MatCano/ixc-odoo-bridge` (convite ao novo responsável).
- Easypanel (painel do servidor da Mais Fibra).
- Odoo: usuário administrador.
- IXC: usuário/token da API (Configurações > Webservice).
- OPA Suíte / Chatwoot (se forem integrados).
- Futuro: conta SMTP, Evolution API, Meta Business.
