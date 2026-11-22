#!/bin/bash
# build_rns_manager.sh - Build eseguibile standalone del Manager
#
# Uso:
#   ./build_rns_manager.sh
#   PYTHON_BIN=/path/.venv/bin/python ./build_rns_manager.sh
#   DIST_DIR=/tmp/out WORK_DIR=/tmp/work ./build_rns_manager.sh
#
# Il .run include la libreria RNS necessaria all'app, ma usa i comandi
# rnid/rnstatus/rnpath/rnprobe e la configurazione ~/.reticulum che
# l'utente ha già installato nel proprio sistema.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
DIST_DIR="${DIST_DIR:-$REPO_ROOT/dist}"
WORK_DIR="${WORK_DIR:-$SCRIPT_DIR/build}"
MAX_SIZE_MB="${MAX_SIZE_MB:-95}"
NO_INSTALL="${NO_INSTALL:-0}"

echo "🚀 Build RNS Manager"
"$PYTHON_BIN" -c 'import sys; print(f"🐍 Python: {sys.version.split()[0]} ({sys.executable})")'

VERSION="$("$PYTHON_BIN" -c 'from version import APP_VERSION; print(APP_VERSION)')"
echo "📦 Versione: $VERSION"

if ! "$PYTHON_BIN" -c 'import RNS, flask' >/dev/null 2>&1; then
    echo "❌ Dipendenze mancanti per $PYTHON_BIN."
    echo "   Attiva la venv e installa: python -m pip install -r requirements.txt"
    exit 1
fi

if ! "$PYTHON_BIN" -m PyInstaller --version >/dev/null 2>&1; then
    if [ "$NO_INSTALL" = "1" ]; then
        echo "❌ PyInstaller non installato. Installa con:"
        echo "   $PYTHON_BIN -m pip install pyinstaller"
        exit 1
    fi
    echo "📥 Installo PyInstaller..."
    "$PYTHON_BIN" -m pip install --upgrade pyinstaller
fi

mkdir -p "$DIST_DIR" "$WORK_DIR"

"$PYTHON_BIN" -m PyInstaller \
    --noconfirm --clean \
    --distpath "$DIST_DIR" \
    --workpath "$WORK_DIR" \
    "$SCRIPT_DIR/rns_manager.spec"

OUT="$DIST_DIR/rns_manager"
FINAL="$DIST_DIR/rns_manager_v${VERSION}.run"

if [ ! -f "$OUT" ]; then
    echo "❌ Errore: build fallita!"
    exit 1
fi

mv -f "$OUT" "$FINAL"
chmod +x "$FINAL"

SIZE_BYTES=$(wc -c < "$FINAL")
SIZE_MB=$(( SIZE_BYTES / 1024 / 1024 ))

echo ""
echo "✅ Creato: $FINAL"
echo "   Versione: $VERSION"
echo "   Dimensione: $(du -h "$FINAL" | cut -f1) (${SIZE_MB} MB)"
sha256sum "$FINAL"

echo ""
echo "ℹ️  Il .run usa i comandi RNS di sistema (rnid, rnstatus, rnpath, rnprobe)"
echo "   e la configurazione ~/.reticulum dell'utente."

if (( SIZE_MB > MAX_SIZE_MB )); then
    echo "⚠️  ${SIZE_MB} MB > ${MAX_SIZE_MB} MB."
    echo "   Per GitHub usa una Release (limite 2 GB per asset), non il repository (limite 100 MB)."
fi
