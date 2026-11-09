# maisfibra_whatsapp — WhatsApp dentro do Odoo (Odoo 19)

Envia e recebe WhatsApp direto do CRM. Provedor atual: **Evolution API**; a camada
`maisfibra.whatsapp.provider` permite trocar para a **Meta Cloud API** sem mexer no resto.

## O que faz
- Botão verde **WhatsApp** no lead/oportunidade e **Enviar WhatsApp** no contato.
- Toda mensagem (enviada ou recebida) aparece no **chatter** como nota interna.
- Número que mandou mensagem e não tem lead aberto → **cria lead** com origem `WhatsApp`
  no funil configurado.
- Mensagens enviadas pelo celular (fora do Odoo) também ficam registradas.
- Histórico em **CRM > WhatsApp > Mensagens**.

## Instalação no Easypanel
1. **Subir a Evolution API**: no projeto `maisfibra`, `+ Serviço` > Template **Evolution API**.
   Anote a URL pública e a `AUTHENTICATION_API_KEY`.
2. **Criar a instância** (ex.: `maisfibra`) no manager da Evolution e escanear o QR Code
   com o WhatsApp da MaisFibra.
3. **Colocar o módulo no Odoo**: copiar a pasta `maisfibra_whatsapp/` para o diretório de
   addons montado no serviço `odoo` (normalmente `/mnt/extra-addons`) e reiniciar o serviço.
4. No Odoo: ativar modo desenvolvedor > Apps > **Atualizar lista de apps** > instalar
   **MaisFibra - WhatsApp**.
5. **Definições > WhatsApp**: preencher URL, API Key, Instância, um **token** forte para o
   webhook e o funil padrão para leads novos.
6. **Webhook na Evolution** (instância > Webhook):
   - URL: `https://maisfibra-odoo.ys2bw5.easypanel.host/maisfibra/whatsapp/webhook?token=SEU_TOKEN`
   - Evento: `MESSAGES_UPSERT` · "Webhook by Events" **desligado**
   - Se Evolution e Odoo estiverem no mesmo projeto, pode usar o endereço interno do serviço.

## Teste rápido
1. Abra um lead com telefone, clique em **WhatsApp**, envie "teste".
2. Responda do celular → a resposta deve aparecer no chatter do lead em segundos.
3. Mande mensagem de um número desconhecido → deve nascer um lead `[WhatsApp] Nome`.

## Pendências / próximos passos
- [ ] **Validar no ambiente real** (não foi testado contra a instância da MaisFibra).
- [ ] Mídia: hoje imagem/áudio/documento entram só como rótulo (`[imagem]`), sem o arquivo.
- [x] Meta Cloud API: envio de texto e de **templates** (`send_template`), webhook com verificação
      (`GET`) e assinatura `X-Hub-Signature-256` (`POST`) em `/maisfibra/whatsapp/meta`.
      Para usar: Definições > WhatsApp > Provedor = Meta, preencher Phone Number ID, token, verify token
      e app secret; no Meta for Developers, apontar o webhook e assinar o campo `messages`.
      Fora da janela de 24h a Meta só aceita template aprovado — o erro avisa isso.
- [ ] Régua de cobrança e CSAT: **só com Meta** (disparo em volume pela Evolution arrisca
      banimento do número). Depende de dados de fatura do IXC via bridge.
- [ ] Desligar o OPA somente depois que a captação de leads estiver estável aqui.
