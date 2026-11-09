# Handoff — Projeto Odoo + IXC (Mais Fibra)

**Projeto:** Implantação Odoo para Mais Fibra — CRM com Integração IXC (MAISFIBRA-ODOO-2026)
**Contratante:** Allnnect · **Cliente final:** Mais Fibra
**Data do handoff:** 09/11/2026 · **Branch com todo o trabalho:** `integracao-bidirecional`

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
1. **Nenhum backup.** O banco roda como app comum (não como serviço Postgres do Easypanel), então não há
   backup de banco; e não há backup de volume em nenhum serviço. Perda do servidor = perda de tudo.
   **Primeira coisa a fazer:** backup de volume diário do `odoo-db` e do `odoo` (filestore) no Easypanel,
   ou `pg_dump` agendado para um storage externo; testar uma restauração.
2. **Bridge sem volume**: o estado do sync (`.ixc_sync_state.json`) some a cada deploy e força
   re-varredura. Criar volume em `/app/data` antes do merge.
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

## 3. O que está pronto (código no branch, **não implantado**)

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
8. Teste ponta a ponta com 1 lead real: Recolher Documentos → Negociação → marcar ganho no IXC.
9. **WhatsApp** (opcional nesta etapa): subir Evolution, escanear QR, configurar em Definições > WhatsApp.

---

## 5. Contrato × situação (Anexo I)

Legenda: ✅ feito · 🟡 código pronto, falta implantar/validar · ⚠️ parcial · ❌ não feito

### Frente 0 — Governança e infraestrutura
| Item | Status | Nota |
|---|---|---|
| PostgreSQL | ✅ | `odoo-db` |
| Ambientes Dev / Homolog / Prod | ❌ | Só produção. Contrato pede cloud (AWS/Azure/GCP) + VPN; hoje é VPS com Easypanel |
| Backup diário, 30 dias | ❌ | **Nenhum backup** (banco como app comum, sem backup de volume) — prioridade 1 |
| Auditoria de alterações | ❌ | Community não tem; usar módulo OCA `auditlog` ou desenvolver |
| Política de governança / LGPD (assinada) | ❌ | |
| Doc. de arquitetura / relatório de segurança | ⚠️ | §1 deste documento é o começo |
| Jira/ClickUp, CI/CD | ❌ | Repositório sem GitHub Actions |

### Frente 1 — CRM + IXC
| Item | Status | Nota |
|---|---|---|
| Odoo instalado, usuários | ✅ | |
| Funil personalizado | ✅ | PF, PJ, Renovação, Website |
| Campos customizados + validações | 🟡 | `maisfibra_crm` |
| Middleware bidirecional (clientes) | 🟡 | Bridge v2 |
| Dados financeiros em tempo real | 🟡 | Botão Consultar IXC |
| Status de serviços | 🟡 | Idem (contrato + status da internet) |
| Testes com 100+ clientes + relatório | ❌ | Fazer após o deploy |
| Dashboards de vendas | 🟡 | Painel de Vendas |
| Relatório de vendedores PDF/Excel | 🟡 | PDF no módulo; Excel pelo pivot |
| Análise de leads | 🟡 | Filtros promissores/em risco |

### Frente 2 — E-mail e WhatsApp
| Item | Status | Nota |
|---|---|---|
| SMTP dedicado | ❌ | Precisa de conta (SendGrid etc.) |
| E-mails automáticos (boas-vindas, cobrança, confirmação) | ❌ | Depende do SMTP |
| Integração WhatsApp | 🟡 | `maisfibra_whatsapp` (Evolution) |
| Disparos automáticos por evento | ❌ | Próximo passo no módulo |
| Chatbot básico + tickets | ❌ | Helpdesk é Enterprise; escalar via atividade no CRM |
| Testes de entrega | ❌ | |

### Frente 3 — Financeiro
Praticamente **não iniciada** (plano de contas padrão carregado; 8 faturas manuais).

---

## 6. Validação no IXC (bloqueia o deploy do fluxo novo)

Confirmado na API: `cliente`, `contato`, `cidade`, `cliente_contrato`, `fn_areceber`;
inserir = POST, alterar = PUT com registro completo; prospect = `cliente` com
`crm`, `status_prospeccao`, `id_condominio`, `bloco`, `apartamento`, `crm_data_*`.

A confirmar na instalação da Mais Fibra:
1. Nome das tabelas de **Condomínios**, **Negociações** e **Planos de negociação** → `IXC_TB_*`.
2. Campos da **negociação** (`odoo_para_ixc.criar_negociacao`).
3. Código de **prospect** (`status_prospeccao`, `ativo`) → `IXC_PROSPECT_FIELDS`.
4. Campos fixos de cliente (filial, tipo de cliente, tipo de assinante…) → `IXC_CLIENTE_DEFAULTS`.
5. Códigos de status de contrato, internet e fatura (`consulta_ixc.py`).
6. Permissões do usuário da API (inserir/alterar em cliente, contato, negociação).

---

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
2. Migrar `x_ID_IXC`/`x_codigo_cliente_ixc` → `mf_ixc_*` e arquivar os campos antigos.
3. Consulta de crédito (aguarda doc da "Central de consultas" do cliente).
4. Mapear `indicado_por`, `id_concorrente`, `id_vendedor` para IDs do IXC (hoje vão em `obs`).
5. WhatsApp: disparos por evento (novo lead, mudança de estágio) e chatbot com menu.
6. SMTP + templates (boas-vindas, confirmações).
7. Auditoria (OCA `auditlog`), CI no GitHub (testes do bridge), backups verificados.
8. Teste com 100+ clientes e relatório de testes (exigido no contrato).

---

## 9. Acessos a repassar (não versionar segredos)

- GitHub `MatCano/ixc-odoo-bridge` (convite ao novo responsável).
- Easypanel (painel do servidor da Mais Fibra).
- Odoo: usuário administrador.
- IXC: usuário/token da API (Configurações > Webservice).
- OPA Suíte / Chatwoot (se forem integrados).
- Futuro: conta SMTP, Evolution API, Meta Business.
