#!/usr/bin/env bash
# update_github.sh - test, commit and push the current branch to GitHub.
#
# Usage:
#   ./tools/update_github.sh [commit message]
#   SKIP_TESTS=1 ./tools/update_github.sh "chore: update docs"
#
# Optional environment variables:
#   GITHUB_REMOTE   git remote name (default: origin)
#   GITHUB_BRANCH   branch to push (default: current branch)
#   SKIP_TESTS      set to 1 to skip local validation
#   DRY_RUN         set to 1 to show what would be committed/pushed

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

REMOTE="${GITHUB_REMOTE:-origin}"
BRANCH="${GITHUB_BRANCH:-$(git branch --show-current)}"
MESSAGE="${1:-Update RNS Manager and RNS Messenger}"
SKIP_TESTS="${SKIP_TESTS:-0}"
DRY_RUN="${DRY_RUN:-0}"

if [[ -z "$BRANCH" ]]; then
    echo "❌ Nessun branch corrente. Specifica GITHUB_BRANCH." >&2
    exit 1
fi

if ! git remote get-url "$REMOTE" >/dev/null 2>&1; then
    echo "❌ Remote '$REMOTE' non configurato." >&2
    exit 1
fi

echo "📦 Repository: $ROOT"
echo "🌿 Branch:     $BRANCH"
echo "🔗 Remote:     $REMOTE ($(git remote get-url "$REMOTE"))"

if [[ "$SKIP_TESTS" != "1" ]]; then
    echo "🧪 Validazione JavaScript inline..."
    python3 tools/check_inline_js.py
    echo "🧪 Test unitari..."
    python3 -m unittest discover -s tests
fi

if [[ -z "$(git status --porcelain)" ]]; then
    echo "ℹ️  Nessuna modifica locale da committare."
else
    echo "📝 Modifiche da includere:"
    git status --short
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "🔎 DRY_RUN=1: nessun commit/push eseguito."
        exit 0
    fi
    git add -A
    git diff --cached --check
    git commit -m "$MESSAGE"
fi

if [[ "$DRY_RUN" == "1" ]]; then
    echo "🔎 DRY_RUN=1: nessun push eseguito."
    exit 0
fi

echo "🚀 Push verso $REMOTE/$BRANCH..."
git push "$REMOTE" "$BRANCH"

echo "✅ Aggiornamento GitHub completato."
