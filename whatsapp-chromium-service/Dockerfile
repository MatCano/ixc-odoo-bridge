# Imagem do serviço WhatsApp Web (Chromium) para o Easypanel.
# Não altera o código do serviço: só empacota o que está nesta pasta.
#
# Easypanel > maisfibra > + Serviço (App) "whatsapp-chromium":
#   Fonte Git: MatCano/ixc-odoo-bridge, branch main, caminho de build /whatsapp-chromium-service
#   Volume compartilhado com o Odoo em /data/whatsapp (no Odoo: /var/lib/odoo/whatsapp)
#   SEM domínio público (só rede interna do Easypanel).
#
# Node 22: o serviço usa node:sqlite (embutido a partir do Node 22.13).
FROM node:22-bookworm-slim

# Chromium do Debian + fontes (inclui emoji/CJK para renderizar o WhatsApp Web)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      chromium fonts-liberation fonts-noto-color-emoji fonts-noto-cjk ca-certificates dumb-init \
 && rm -rf /var/lib/apt/lists/*

ENV NODE_ENV=production \
    PUPPETEER_SKIP_DOWNLOAD=true \
    CHROME_BIN=/usr/bin/chromium \
    PORT=3001

WORKDIR /app

# Dependências primeiro (cache). O postinstall aplica os patches do whatsapp-web.js,
# por isso patches/ e patch-wwebjs.js precisam estar aqui antes do npm ci.
COPY package.json package-lock.json patch-wwebjs.js ./
COPY patches ./patches
RUN npm ci --omit=dev

COPY db.js server.js session-manager.js ./

# Sessões (login do WhatsApp), mídias e o SQLite ficam em /data/whatsapp
# (o código usa ../data/whatsapp a partir de /app). Montar volume aqui.
RUN mkdir -p /data/whatsapp/sessions /data/whatsapp/media
VOLUME ["/data/whatsapp"]

EXPOSE 3001

# dumb-init repassa sinais e recolhe processos órfãos do Chromium.
# O Easypanel reinicia o container se o processo cair (substitui o daemon.sh).
ENTRYPOINT ["dumb-init", "--"]
CMD ["sh", "-c", "find /data/whatsapp/sessions -name 'Singleton*' -delete 2>/dev/null; exec node server.js"]
