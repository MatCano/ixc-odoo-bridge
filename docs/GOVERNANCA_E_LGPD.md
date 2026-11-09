# Política de Governança de Dados e Conformidade com a LGPD
## Sistema CRM Odoo integrado ao IXC — Mais Fibra

**Versão:** 1.0 — RASCUNHO PARA REVISÃO E ASSINATURA
**Data:** 09/11/2026
**Entregável:** Frente 0 do Anexo I ("Documento de Políticas de Governança de Dados — assinado")

> Rascunho técnico preparado pela equipe de implantação. Deve ser revisado pela Mais Fibra (e, se possível,
> por seu encarregado de dados/jurídico) antes da assinatura. Não constitui parecer jurídico.

---

### 1. Objetivo e escopo
Definir como os dados pessoais e corporativos tratados no CRM Odoo, no middleware de integração e nos canais
de comunicação (e-mail e WhatsApp) são acessados, classificados, protegidos, retidos e descartados, em
conformidade com a Lei nº 13.709/2018 (LGPD).

Abrange: Odoo (CRM, Contatos, Faturamento), banco de dados, middleware `ixc-odoo-bridge`, integrações com o
IXC Provedor, OPA Suíte/Chatwoot, provedor de WhatsApp e servidor de e-mail.

### 2. Papéis
| Papel | Quem (preencher) | Responsabilidades |
|---|---|---|
| Controlador | Mais Fibra | Define finalidades e bases legais |
| Encarregado (DPO) | ______________ | Canal com titulares e ANPD |
| Operador — implantação/sustentação | Allnnect e prestadores | Trata dados conforme instruções do controlador |
| Suboperadores | Hospedagem (VPS/Easypanel), provedor de WhatsApp, provedor de e-mail | Listados no item 9 |
| Administrador do sistema | ______________ | Concede/revoga acessos, revisa auditoria |

### 3. Inventário e classificação dos dados
| Categoria | Exemplos | Classificação | Onde |
|---|---|---|---|
| Identificação | nome, CPF/CNPJ, e-mail, telefone | **Pessoal** | Odoo, IXC |
| Localização | endereço de instalação, condomínio, bloco, apto | **Pessoal** | Odoo, IXC |
| Financeiro | faturas, valores em aberto/vencidos, status do contrato | **Pessoal — restrito** | IXC (Odoo exibe resumo) |
| Crédito | resultado de análise de crédito | **Pessoal — restrito** | IXC / Odoo (resumo) |
| Comunicações | mensagens de WhatsApp e e-mails | **Pessoal** | Odoo |
| Comercial | estágio, origem, vendedor, valores esperados | Interno | Odoo |
| Técnicos | logs de integração e auditoria | Interno — restrito | Servidor / Odoo |

Não há tratamento previsto de **dados pessoais sensíveis** (art. 5º, II). Caso surja, exige aprovação do
encarregado antes.

### 4. Finalidades e bases legais
| Finalidade | Base legal (art. 7º) |
|---|---|
| Atender pedido de contratação, propor e formalizar o serviço | Execução de contrato / procedimentos preliminares (V) |
| Cobrança e gestão do contrato | Execução de contrato (V) |
| Análise de crédito | Proteção do crédito (X) |
| Mensagens de boas-vindas, confirmação e status | Execução de contrato (V) |
| Comunicação comercial a quem ainda não é cliente | Legítimo interesse (IX), com opção de descadastro |
| Registros de auditoria e segurança | Legítimo interesse (IX) / obrigação legal (II) |

### 5. Controle de acesso (RBAC)
| Perfil (grupo Odoo) | Acesso |
|---|---|
| Vendedor (`Vendas: Somente meus documentos`) | Seus leads e contatos; consulta IXC dos seus clientes |
| Gerente de vendas (`Vendas: Administrador`) | Todos os leads; relatórios; leitura da auditoria |
| Financeiro (`Faturamento`) | Faturas e cobrança |
| Administrador (`Configurações`) | Configuração, usuários, integrações, auditoria |

Regras:
1. Acesso por **usuário nominal**; proibido compartilhar login.
2. Menor privilégio: cada pessoa recebe só o perfil necessário à função.
3. Concessão e revogação registradas pelo administrador; **revogação no mesmo dia** do desligamento.
4. **Revisão trimestral** de usuários e perfis.
5. Contas de integração (bridge) com usuário técnico próprio, sem uso por pessoas.
6. Senhas fortes; autenticação em dois fatores para administradores (Odoo suporta TOTP).

### 6. Segurança da informação
- Credenciais e tokens apenas em variáveis de ambiente/parâmetros protegidos — **nunca em código ou Git**.
- Comunicação por HTTPS; integrações internas pela rede privada do servidor.
- Repositório de código **privado**.
- Auditoria de alterações ativa (quem, quando, valor anterior e novo) em leads, contatos, faturas e estágios.
- Backups diários com retenção de 30 dias e **teste de restauração mensal**; cópia em armazenamento externo.
- Atualizações de segurança do Odoo/PostgreSQL aplicadas em até 30 dias.

### 7. Retenção e descarte
| Dado | Retenção |
|---|---|
| Leads não convertidos | 24 meses após o último contato; depois, anonimizar ou excluir |
| Clientes | Durante o contrato + prazos legais (fiscal/contábil: 5 anos; consumidor: 5 anos) |
| Mensagens de WhatsApp/e-mail | 24 meses |
| Registros de auditoria | Mínimo 12 meses (sistema mantém 13) |
| Logs técnicos de integração | 90 dias |
| Backups | 30 dias |

### 8. Direitos dos titulares (art. 18)
Pedidos de acesso, correção, anonimização, portabilidade, eliminação e revogação de consentimento chegam pelo
canal do encarregado: `______________`.
- Prazo de resposta: **15 dias**.
- Correção: no IXC (fonte) — o Odoo é atualizado pela integração.
- Eliminação: avaliar retenção legal; quando cabível, excluir/anonimizar no IXC e no Odoo e registrar o
  atendimento.
- Descadastro de mensagens comerciais: marcar o contato e desligar automações para ele.

### 9. Suboperadores e transferências
| Serviço | Finalidade | Localização dos dados |
|---|---|---|
| Hospedagem (VPS/Easypanel) | Servidor do Odoo, banco e bridge | ______________ |
| IXC Soft | ERP do provedor | ______________ |
| Provedor de WhatsApp (Evolution/Meta) | Mensagens | ______________ |
| Provedor de e-mail (SMTP) | E-mails transacionais | ______________ |
| GitHub | Código-fonte (sem dados pessoais) | EUA |

Transferência internacional só com as garantias do art. 33.

### 10. Incidentes de segurança
1. Quem identificar comunica o administrador e o encarregado **imediatamente**.
2. Contenção: revogar acessos/tokens afetados, isolar o serviço.
3. Avaliação de risco aos titulares em até 24 h.
4. Comunicação à ANPD e aos titulares, quando houver risco ou dano relevante, no prazo regulamentar.
5. Registro do incidente e das medidas (usar a auditoria e os logs como evidência).

### 11. Revisão
Esta política é revisada anualmente ou quando houver mudança relevante no sistema.

---

**Aprovações**

| | Nome | Cargo | Assinatura | Data |
|---|---|---|---|---|
| Mais Fibra (Controlador) | | | | |
| Encarregado (DPO) | | | | |
| Allnnect (Operador) | | | | |
