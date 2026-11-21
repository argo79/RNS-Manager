#!/bin/bash
# start.sh - Avvia RNS Manager & Aspect Monitor

set -e
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "🚀 Avvio RNS Manager (http://127.0.0.1:5010)..."
exec "$PYTHON_BIN" rns_manager.py

