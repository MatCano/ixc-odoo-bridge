# Plano de Testes — Integração IXC ↔ Odoo e Automações

Versão 1.0 · 09/11/2026 · Atende aos entregáveis "Testes de Integração IXC-Odoo" (Frente 1, sem. 7) e
"Testes de Entrega e Performance" (Frente 2, sem. 14) do Anexo I.

**Como usar:** execute na ordem, marque o resultado e anexe evidência (print, ID do registro, linha de log).
Ao final, o quadro do item 6 vira o **Relatório de Testes** pedido no contrato.

Pré-requisitos: backup recente (Easypanel > odoo-db > Armazenamento > Execução Manual) · bridge com
`MF_CAMPOS=1` · validações do IXC feitas (HANDOFF §6).

---

## 1. Testes automatizados (já existentes)
| ID | O quê | Como | Esperado |
|---|---|---|---|
| A1 | 13 cenários do fluxo com IXC/Odoo simulados | `python tests/test_fluxo.py` (roda no CI a cada push) | 13 × OK |
| A2 | Estrutura dos módulos (XML, Python, manifestos) | GitHub Actions | Verde |

## 2. Instalação e migração
| ID | Passo | Esperado | Resultado |
|---|---|---|---|
| I1 | Instalar `maisfibra_crm`, `maisfibra_whatsapp`, `maisfibra_auditoria` | Sem erro; menus MaisFibra, WhatsApp, Auditoria | ✅ crm e whatsapp instalados em 09/11 |
| I2 | Definições > MaisFibra > **Migrar agora** | Mensagem com ~3.000 leads atualizados | |
| I3 | Abrir 5 leads antigos | Aba "Cliente & Instalação" com CPF e IDs do IXC | |
| I4 | Rodar "Migrar agora" de novo | 0 atualizados (idempotente) | |

## 3. Integração IXC → Odoo
| ID | Passo | Esperado | Resultado |
|---|---|---|---|
| E1 | Cadastrar lead de teste no IXC (CPF de teste) | Em ≤ 2 min aparece no Odoo, funil B2C | |
| E2 | Idem com CNPJ | Funil B2B | |
| E3 | Alterar telefone do lead no IXC | Odoo atualiza; se o vendedor já tinha editado o campo de endereço, **não sobrescreve** | |
| E4 | Reiniciar o bridge (Implantar) | Não reimporta tudo (estado em `/app/data`) | |
| E5 | Condomínio cadastrado no IXC | Em ≤ 60 min aparece em CRM > Configuração > Condomínios | |
| E6 | Marcar **vencemos** no prospect de teste no IXC | Lead vai para "Aguardar Assinatura" + nota no chatter | |
| E7 | Marcar **perdemos** em outro | Lead vai para "Perdemos" | |

## 4. Integração Odoo → IXC
| ID | Passo | Esperado | Resultado |
|---|---|---|---|
| S1 | Mover lead **sem e-mail** para Recolher Documentos | Bloqueia, lista campos faltando, estágio não muda | |
| S2 | Preencher todos os campos e mover | Prospect criado no IXC; ID aparece no lead; nota "Prospect salvo" | |
| S3 | Conferir no IXC | Cliente com dados, condomínio, bloco, apto, obs com origem; contato vinculado | |
| S4 | Mover o mesmo lead de novo | **Não duplica** cliente no IXC (atualiza) | |
| S5 | Mover para Negociação sem plano | Bloqueia | |
| S6 | Com plano e descrição | Negociação criada no IXC; ID no lead | |
| S7 | Forçar recusa do IXC (ex.: cidade inexistente) | Mensagem do IXC aparece ao vendedor; estágio não muda | |
| S8 | Botão **Consultar IXC** num cliente real | Faturas, contratos e status do serviço batem com o IXC | |
| S9 | Lead externo: `POST /webhook/lead-externo` | Contato no IXC + lead no Odoo com "Como nos conheceu" | |

## 5. Roteiro de volume — 100+ clientes (exigido no contrato)
1. Separar **100 clientes reais** do IXC (mistura de PF/PJ, ativos/bloqueados, com e sem fatura vencida).
2. Exportar a lista do IXC (id, CPF/CNPJ, status do contrato, nº de faturas em aberto, valor em aberto).
3. Para cada um, no Odoo: buscar pelo CPF → conferir que existe contato/lead vinculado (`ID Cliente (IXC)`).
4. Rodar **Consultar IXC** em 20 deles (amostra) e comparar status do serviço e valor em aberto.
5. Medir: tempo médio da consulta (meta: < 3 s, requisito de performance do contrato).
6. Registrar divergências na planilha (coluna "motivo": sem CPF no IXC, CPF formatado diferente etc.).

Critério de aceite: ≥ 98% dos 100 clientes vinculados corretamente; 100% da amostra com valores iguais
ao IXC; nenhuma duplicidade.

## 6. Automações (Frente 2)
| ID | Passo | Esperado | Resultado |
|---|---|---|---|
| W1 | WhatsApp: enviar pelo botão do lead | Chega no celular; registro no chatter | |
| W2 | Responder do celular | Resposta no chatter em segundos | |
| W3 | Número desconhecido manda "oi" | Lead `[WhatsApp]` criado, origem WhatsApp | |
| W4 | Ligar chatbot; mandar "oi" | Recebe o menu | |
| W5 | Responder "3" | Mensagem de encaminhamento + atividade para o vendedor; bot fica quieto | |
| W6 | Mensagem no estágio + automação ligada; mover lead | Cliente recebe a mensagem do estágio | |
| W7 | Volume: 50 mensagens para números da equipe | Todas "Enviada" na fila; nenhuma "Erro" | |
| M1 | SMTP configurado; e-mail de boas-vindas ligado; criar contato com e-mail | E-mail recebido | |
| M2 | Template no estágio "Apresentação da Proposta"; mover lead | E-mail de proposta recebido | |
| M3 | Volume: 100 e-mails para caixas da equipe | Todos entregues (Configurações > Técnico > E-mails) | |
| AU1 | Alterar telefone de um lead | Definições > Auditoria mostra antes/depois e usuário | |
| AU2 | Tentar editar um registro de auditoria | Bloqueado | |

## 7. Relatório de testes (preencher ao final)
| Bloco | Total | OK | Falhou | Observações |
|---|---|---|---|---|
| Instalação/migração | 4 | | | |
| IXC → Odoo | 7 | | | |
| Odoo → IXC | 9 | | | |
| Volume 100+ | 1 | | | |
| Automações e auditoria | 12 | | | |

Executado por: ______________ · Período: ___/___ a ___/___ · Aceite Mais Fibra: ______________
