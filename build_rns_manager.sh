#!/bin/bash
# build.sh - UN SOLO FILE!

echo "🚀 Build RNID Manager"

# Installa pyinstaller
pip install pyinstaller

# 🔥 Build CON --collect-all per Reticulum
pyinstaller --onefile \
    --add-data "templates:templates" \
    --add-data "static:static" \
    --add-data "modules:modules" \
    --collect-all "RNS" \
    --collect-all "reticulum" \
    --hidden-import "RNS.Interfaces.LocalInterface" \
    --hidden-import "RNS.Interfaces.TCPInterface" \
    --name "rns_manager" \
    --console \
    rns_manager.py

# Copia nella root
cp dist/rns_manager ./

echo "✅ Fatto! Eseguibile: ./rns_manager"A