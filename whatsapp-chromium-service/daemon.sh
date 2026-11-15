#!/usr/bin/env bash
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

while true; do
  echo "[$(date -Iseconds)] Iniciando WhatsApp Chromium Service..."
  # Limpa travas do Chromium residuais
  find ../data/whatsapp/sessions -name "Singleton*" -delete 2>/dev/null || true
  node server.js || true
  echo "[$(date -Iseconds)] WhatsApp Chromium Service parou. Reiniciando em 2 segundos..."
  sleep 2
done
