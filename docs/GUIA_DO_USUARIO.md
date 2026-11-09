# Guia do Usuário — CRM Mais Fibra (Odoo)

Para a equipe comercial, gerentes e financeiro. Versão 1.0 · novembro/2026.

> Algumas funções ficam ativas só depois que a TI liga a integração e as automações. Onde for o caso,
> está marcado com 🔌.

---

## 1. O lead por dentro

Abra qualquer lead em **CRM**. Além do que já existia, há:

### Aba "Cliente & Instalação"
| Campo | Para que serve |
|---|---|
| **CPF/CNPJ** | Obrigatório para virar prospect no IXC. Só números ou formatado, tanto faz |
| **Como nos conheceu** | De onde veio o cliente (Instagram, indicação, panfleto…). Vem sozinho de WhatsApp/OPA |
| **Canal de venda**, **Indicado por**, **Concorrente** | Para os relatórios de origem e para o IXC |
| **Condomínio** | Escolha da lista (vem do IXC). Ao escolher, endereço, CEP e cidade são preenchidos |
| **Bloco / Apartamento / CEP / Endereço / Número / Bairro / Cidade** | Endereço **de instalação** (pode ser diferente do endereço de cobrança do contato) |
| **Financeiro e serviço (IXC)** | Resumo da última consulta: status da internet, faturas em aberto/vencidas |

### Botões no topo
| Botão | O que faz |
|---|---|
| **WhatsApp** (verde) 🔌 | Abre uma janela para mandar mensagem ao cliente. A conversa fica no histórico do lead |
| **Consultar IXC** 🔌 | Mostra na hora as **faturas** (vencidas em vermelho, com linha digitável) e os **contratos** com o status da internet (ativo, bloqueado, em atraso) |
| **Consultar crédito** | Em implantação |

---

## 2. Andando com o lead no funil 🔌

Arraste o lead entre as colunas como sempre. Dois estágios fazem coisas no IXC automaticamente:

### ➜ Recolher Documentos
O Odoo **salva o cliente como prospect no IXC**. Para isso, precisa estar preenchido:
**e-mail, CPF/CNPJ, condomínio, bloco, apartamento, CEP, endereço e cidade.**

- Se faltar algo, aparece uma mensagem dizendo **o que falta** e o lead **não muda** de coluna.
- Se o IXC recusar (ex.: cidade não encontrada), a mensagem do IXC aparece e o lead também não muda.
- Deu certo: aparece no histórico "✅ Prospect salvo no IXC (ID …)".

### ➜ Negociação
O Odoo **cria a negociação no IXC**. Precisa de: **plano**, **descrição do produto**, cidade, CPF/CNPJ e e-mail.
Preencha na aba **Negociação** (aparece a partir de Recolher Documentos).

### Ganhou ou perdeu?
Quem fecha é o **IXC**. Quando a negociação é marcada como **vencemos** ou **perdemos** lá, o lead vai sozinho
para **Aguardar Assinatura** ou **Perdemos** no Odoo (em até alguns minutos). Não precisa mover à mão.

---

## 3. WhatsApp 🔌

| Situação | O que acontece |
|---|---|
| Você manda pelo botão **WhatsApp** | Mensagem sai pelo número da Mais Fibra e fica no histórico |
| O cliente responde | A resposta aparece no histórico do lead em segundos |
| Número desconhecido manda mensagem | Nasce um lead **[WhatsApp] Nome**, com origem WhatsApp |
| Chatbot ligado | O cliente recebe um menu (1 contratar · 2 já sou cliente · 3 atendente). Se escolher 1 ou 3, **você recebe uma atividade "Responder WhatsApp"** e o robô para de responder aquele cliente |
| Mudança de estágio | Se o estágio tiver mensagem configurada, o cliente recebe (ex.: "Recebemos seus documentos!") |

Histórico geral: **CRM > WhatsApp > Mensagens**.

---

## 4. Pesquisa de satisfação (CSAT) 🔌

- Quando o lead chega ao estágio marcado (ex.: após a instalação), o cliente recebe um **link** por WhatsApp
  e/ou e-mail: nota de 1 a 5 e um comentário.
- A nota aparece no lead (aba **Satisfação**) e no histórico com estrelas.
- **Nota 1 ou 2** cria uma atividade para o **gerente** ligar para o cliente.
- Resultados: **CRM > Relatórios > MaisFibra > Satisfação (CSAT)**.

---

## 5. Relatórios (CRM > Relatórios > MaisFibra)

| Relatório | Para quê | Dica |
|---|---|---|
| **Painel de Vendas** | Leads, ganhos, perdidos e valor vendido por mês | Troque o agrupamento por Vendedor, Funil ou Origem |
| **Desempenho de Vendedores** | Tabela por vendedor e mês | Botão de download exporta para **Excel** |
| **Leads por Origem** | Qual canal traz mais lead | |
| **Análise de Leads** | Lista do que precisa de atenção hoje | Filtros: **Promissores**, **Parados há +7 dias**, **Atividade atrasada**, **Sem próxima atividade**, **Com fatura vencida no IXC** |
| **Relatório de Vendedores (PDF)** | Conversão, ticket médio e ciclo de venda por vendedor | Escolha o período e os funis → Gerar PDF |

**Rotina sugerida para o gerente (5 min/dia):** abrir *Análise de Leads* → filtro *Parados há +7 dias* →
cobrar o próximo passo de cada um.

---

## 6. Financeiro (Faturamento) — para o time financeiro

| Onde | O que faz |
|---|---|
| **Fornecedores > MaisFibra > Contas a Aprovar** | Contas a pagar aguardando aprovação. Botões **Aprovar** / **Recusar** (com motivo) |
| Conta a pagar (rascunho) > **Solicitar aprovação** 🔌 | Envia para quem tem a alçada: **até R$ 5 mil Gerente · até R$ 20 mil Diretor · acima CFO**. Sem aprovação, a conta **não pode ser lançada**; se o valor mudar depois, precisa aprovar de novo |
| **Fornecedores > MaisFibra > Agenda de Pagamentos** | Calendário de vencimentos; filtros *próximos 7 dias* e *atrasados* |
| **Relatórios > MaisFibra > Fluxo de Caixa Previsto** | Quanto entra e sai por mês de vencimento (do que está no Odoo) |
| **Relatórios > MaisFibra > DRE Gerencial** | Receitas, custos, despesas e resultado por mês; exporta para Excel |

> As faturas dos clientes do provedor continuam no **IXC**. No Odoo você as vê pelo botão **Consultar IXC**.

---

## 7. Perguntas frequentes

**O lead não muda de coluna e aparece uma mensagem.** — Leia a mensagem: ela diz qual campo falta ou o que
o IXC respondeu. Preencha e tente de novo.

**Alterei o endereço no Odoo e o IXC sobrescreveu?** — Não. O Odoo só recebe do IXC o que está **vazio**;
o que você preencheu é mantido.

**Quem alterou esse lead?** — Gerentes: **Configurações > Auditoria > Registro de alterações** mostra quem,
quando e o valor antes/depois.

**A integração caiu?** — A TI recebe aviso automático no canal **Integração IXC** (Discuss). Se você notar
algo estranho, avise no mesmo canal.

**Esqueci de preencher o condomínio e ele não está na lista.** — Os condomínios vêm do IXC (atualização a cada
hora). Peça o cadastro no IXC.
