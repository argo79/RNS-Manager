#!/bin/bash

set -e

# directory dove si trova questo script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# python disponibile nel sistema
PYTHON_BIN="$(command -v python3)"

# storage Reticulum nella home dell'utente corrente
RNS_STORAGE="$HOME/.rns_manager/storage/Prova1"

cd "$SCRIPT_DIR"

echo "Avvio primo script..."
"$PYTHON_BIN" rns_manager.py &

sleep 6

echo "Avvio secondo script..."
"$PYTHON_BIN" lxmf_chat.py -i "$RNS_STORAGE" &

wait