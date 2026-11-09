# maisfibra_crm (Odoo 19)

Fluxo comercial da MaisFibra no CRM. Depende do serviço **ixc-odoo-bridge** para falar com o IXC.

## O que adiciona
- **Contato e Lead**: endereço de instalação (separado do endereço padrão), condomínio, bloco, apartamento.
- **Lead**: CPF/CNPJ, como nos conheceu (origem), canal de venda, indicado por, concorrente,
  IDs do IXC (lead, prospect, negociação), análise de crédito.
- **Abas**: "Cliente & Instalação" e "Negociação" (a partir de Recolher Documentos).
- **Estágios com regra**: NÃO cria estágios. Marque a "Regra MaisFibra" nos estágios que já existem em cada funil (Recolher Documentos, Negociação, Aguardar Assinatura, Perdemos).
  A regra fica no campo **Regra MaisFibra** do estágio — renomear não quebra nada.
- **Cadastros** (CRM > Configuração > MaisFibra): Condomínios e Planos (sincronizados do IXC),
  Canais de venda (manual).

## Regras
- Entrar em **Recolher Documentos** exige: e-mail, CPF/CNPJ, condomínio, bloco, apto, CEP,
  endereço, cidade → salva o **prospect** no IXC. Se faltar algo ou o IXC recusar, o lead não muda.
- Entrar em **Negociação** exige: cidade, CPF/CNPJ, e-mail, plano, descrição do produto, e já ser
  prospect → cria a **negociação** no IXC.
- Ganho/perda vêm do IXC e movem o lead sozinhos.

## Instalação
1. Pasta `maisfibra_crm/` no diretório de addons do Odoo, reiniciar, Apps > Atualizar lista > instalar.
2. Definições > **MaisFibra**: URL e token do bridge.
3. CRM > Configuração > Estágios: em cada funil, abrir Recolher Documentos e escolher a Regra MaisFibra; idem Negociação, Aguardar Assinatura (ganho) e Perdemos (criar os que não existirem).
