#!/bin/bash
# build_rns_manager.sh - Build eseguibile standalone del Manager

set -e
cd "$(dirname "$0")"

echo "🚀 Build RNS Manager"

# Leggi la versione da version.py
VERSION=$(python3 -c "from version import APP_VERSION; print(APP_VERSION)")
echo "📦 Versione: $VERSION"

# Installa pyinstaller
pip install pyinstaller

# Build CON --collect-all per Reticulum
pyinstaller --onefile \
    --add-data "templates:templates" \
    --add-data "static:static" \
    --add-data "modules:modules" \
    --add-data "version.py:." \
    --collect-all "RNS" \
    --collect-all "reticulum" \
    --hidden-import "RNS.Interfaces.LocalInterface" \
    --hidden-import "RNS.Interfaces.TCPInterface" \
    --name "rns_manager" \
    --console \
    rns_manager.py

# Crea eseguibile con versione
if [ -f "dist/rns_manager" ]; then
    mv dist/rns_manager "dist/rns_manager_v${VERSION}.run"
    chmod +x "dist/rns_manager_v${VERSION}.run"
    echo "✅ Fatto! Eseguibile: ./dist/rns_manager_v${VERSION}.run"
    echo ""
    echo "📊 Info:"
    echo "   Versione: $VERSION"
    echo "   File: $(ls -lh "dist/rns_manager_v${VERSION}.run" | awk '{print $5}')"
    echo "   Percorso: $(pwd)/dist/rns_manager_v${VERSION}.run"
else
    echo "❌ Errore: build fallita!"
    exit 1
fi
