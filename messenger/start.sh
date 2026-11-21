#!/bin/bash
# start.sh - Avvia RNS Messenger

set -e
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
PORT="${LXMF_PORT:-5012}"
HOST="${LXMF_HOST:-127.0.0.1}"

echo "🚀 Avvio RNS Messenger (http://${HOST}:${PORT})..."

# Passa l'identità con: ./start.sh -i ~/.rns_manager/storage/<identita>
exec "$PYTHON_BIN" lxmf_chat.py --host "$HOST" -p "$PORT" "$@"
