#!/usr/bin/env bash
# update_github.sh - test, commit, push and optionally publish .run assets.
#
# Usage:
#   ./tools/update_github.sh [options] [commit message]
#
# Options:
#   --all          upload every dist/*.run file to the GitHub Release
#   --run          upload the latest .run for manager and messenger
#   --no-run       never upload .run files
#   --dry-run      show what would be committed/uploaded
#   --skip-tests   skip local validation
#   -h, --help     show this help
#
# Without --run/--all/--no-run, the script asks whether to upload the
# latest manager/messenger bundles to the GitHub Release.
#
# Optional environment variables:
#   GITHUB_REMOTE       git remote name (default: origin)
#   GITHUB_BRANCH       branch to push (default: current branch)
#   GITHUB_RELEASE_TAG  release tag (default: v<APP_VERSION>)
#   SKIP_TESTS          set to 1 to skip local validation
#   DRY_RUN             set to 1 to show what would be done

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

REMOTE="${GITHUB_REMOTE:-origin}"
BRANCH="${GITHUB_BRANCH:-$(git branch --show-current)}"
SKIP_TESTS="${SKIP_TESTS:-0}"
DRY_RUN="${DRY_RUN:-0}"
INCLUDE_MODE="ask"
ARGS=()

usage() {
    sed -n '1,30p' "$0" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
    case "$arg" in
        --all) INCLUDE_MODE="all" ;;
        --run|--with-run) INCLUDE_MODE="latest" ;;
        --no-run) INCLUDE_MODE="none" ;;
        --dry-run) DRY_RUN="1" ;;
        --skip-tests) SKIP_TESTS="1" ;;
        -h|--help) usage; exit 0 ;;
        --*) echo "❌ Opzione sconosciuta: $arg" >&2; usage; exit 2 ;;
        *) ARGS+=("$arg") ;;
    esac
done

MESSAGE="${ARGS[0]:-Update RNS Manager and RNS Messenger}"

if [[ -z "$BRANCH" ]]; then
    echo "❌ Nessun branch corrente. Specifica GITHUB_BRANCH." >&2
    exit 1
fi

if ! git remote get-url "$REMOTE" >/dev/null 2>&1; then
    echo "❌ Remote '$REMOTE' non configurato." >&2
    exit 1
fi

VERSION="$(sed -n 's/^APP_VERSION *= *"\(.*\)"/\1/p' messenger/version.py | head -1)"
RELEASE_TAG="${GITHUB_RELEASE_TAG:-v${VERSION:-1.2.0}}"
RUN_DIR="$ROOT/dist"

mapfile -t ALL_RUNS < <(
    find "$RUN_DIR" -maxdepth 1 -type f -name '*.run' -printf '%p\n' 2>/dev/null | sort -V
)

latest_run() {
    local pattern="$1"
    local name
    name="$(find "$RUN_DIR" -maxdepth 1 -type f -name "$pattern" -printf '%f\n' 2>/dev/null | sort -V | tail -1)"
    [[ -n "$name" ]] && printf '%s\n' "$RUN_DIR/$name"
}

LATEST_RUNS=()
for pattern in 'rns_manager_*.run' 'rns_messenger_*.run'; do
    run_file="$(latest_run "$pattern")"
    [[ -n "$run_file" ]] && LATEST_RUNS+=("$run_file")
done

SELECTED_RUNS=()
case "$INCLUDE_MODE" in
    all)
        SELECTED_RUNS=("${ALL_RUNS[@]}")
        ;;
    latest)
        SELECTED_RUNS=("${LATEST_RUNS[@]}")
        ;;
    none)
        ;;
    ask)
        if [[ ${#LATEST_RUNS[@]} -gt 0 && "$DRY_RUN" != "1" && -t 0 ]]; then
            echo "📦 Bundle .run disponibili:"
            for run_file in "${LATEST_RUNS[@]}"; do
                printf '   - %s (%s)\n' "$(basename "$run_file")" "$(du -h "$run_file" | cut -f1)"
            done
            read -r -p "Caricare i bundle aggiornati nella Release $RELEASE_TAG? [y/N] " answer
            if [[ "$answer" =~ ^[Yy]$ ]]; then
                SELECTED_RUNS=("${LATEST_RUNS[@]}")
            fi
        elif [[ ${#LATEST_RUNS[@]} -gt 0 ]]; then
            echo "ℹ️  Bundle .run trovati; usa --run per caricarli o --all per tutti."
        fi
        ;;
esac

echo "📦 Repository: $ROOT"
echo "🌿 Branch:     $BRANCH"
echo "🔗 Remote:     $REMOTE ($(git remote get-url "$REMOTE"))"
echo "🏷️  Release:    $RELEASE_TAG"

if [[ "$SKIP_TESTS" != "1" ]]; then
    echo "🧪 Validazione JavaScript inline..."
    python3 tools/check_inline_js.py
    echo "🧪 Test unitari..."
    python3 -m unittest discover -s tests
fi

echo "📝 Stato Git:"
git status --short

if [[ -n "$(git status --porcelain)" || ${#SELECTED_RUNS[@]} -gt 0 ]]; then
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "🔎 DRY_RUN=1: nessun commit/push/upload eseguito."
        if [[ ${#SELECTED_RUNS[@]} -gt 0 ]]; then
            echo "   Bundle da caricare (${#SELECTED_RUNS[@]}):"
            for run_file in "${SELECTED_RUNS[@]}"; do
                printf '   - %s\n' "$(basename "$run_file")"
            done
        fi
        exit 0
    fi
    git add -A
    if git diff --cached --quiet; then
        echo "ℹ️  Nessuna modifica da committare."
    else
        git diff --cached --check
        git commit -m "$MESSAGE"
    fi
else
    echo "ℹ️  Nessuna modifica locale da committare."
fi

if [[ "$DRY_RUN" == "1" ]]; then
    echo "🔎 DRY_RUN=1: nessun push/upload eseguito."
    exit 0
fi

echo "🚀 Push verso $REMOTE/$BRANCH..."
git push "$REMOTE" "$BRANCH"

upload_runs() {
    if [[ ${#SELECTED_RUNS[@]} -eq 0 ]]; then
        return 0
    fi
    if ! command -v gh >/dev/null 2>&1; then
        echo "❌ gh CLI non disponibile: impossibile caricare i .run nella Release." >&2
        return 1
    fi
    if ! gh release view "$RELEASE_TAG" >/dev/null 2>&1; then
        echo "❌ Release '$RELEASE_TAG' non trovata su GitHub." >&2
        return 1
    fi

    local uploads=()
    local names=()
    for run_file in "${SELECTED_RUNS[@]}"; do
        local base
        base="$(basename "$run_file")"
        names+=("$base")
        uploads+=("$run_file")
        (cd "$RUN_DIR" && sha256sum "$base" > "$base.sha256")
        uploads+=("$RUN_DIR/$base.sha256")
    done
    (cd "$RUN_DIR" && sha256sum "${names[@]}" > SHA256SUMS)
    uploads+=("$RUN_DIR/SHA256SUMS")

    echo "📤 Caricamento bundle nella Release $RELEASE_TAG..."
    gh release upload "$RELEASE_TAG" "${uploads[@]}" --clobber
}

upload_runs

echo "✅ Aggiornamento GitHub completato."
