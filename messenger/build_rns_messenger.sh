#!/bin/bash
# build_rns_messenger.sh - Build eseguibile standalone del Messenger

set -e
cd "$(dirname "$0")"

echo "🚀 Build RNS Messenger"

# Leggi la versione da version.py
VERSION=$(python3 -c "from version import APP_VERSION; print(APP_VERSION)")
echo "📦 Versione: $VERSION"

# Installa pyinstaller
pip install pyinstaller

# Build CON --collect-all per Reticulum e LXMF
pyinstaller --onefile \
    --add-data "templates:templates" \
    --add-data "static:static" \
    --add-data "version.py:." \
    --collect-all "RNS" \
    --collect-all "LXMF" \
    --collect-all "LXST" \
    --hidden-import "flask_socketio" \
    --name "rns_messenger" \
    --console \
    lxmf_chat.py

# Crea eseguibile con versione
if [ -f "dist/rns_messenger" ]; then
    mv dist/rns_messenger "dist/rns_messenger_v${VERSION}.run"
    chmod +x "dist/rns_messenger_v${VERSION}.run"
    echo "✅ Fatto! Eseguibile: ./dist/rns_messenger_v${VERSION}.run"
    echo ""
    echo "📊 Info:"
    echo "   Versione: $VERSION"
    echo "   File: $(ls -lh "dist/rns_messenger_v${VERSION}.run" | awk '{print $5}')"
    echo "   Percorso: $(pwd)/dist/rns_messenger_v${VERSION}.run"
else
    echo "❌ Errore: build fallita!"
    exit 1
fi
