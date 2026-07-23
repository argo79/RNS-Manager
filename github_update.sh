#!/bin/bash
# github_update.sh - Aggiorna repository GitHub e release
# Questo file viene automaticamente ignorato da git

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║     🚀 GITHUB UPDATE & RELEASE SCRIPT                          ║"
echo "║     by Arg0net                                                 ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

# Colori
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

VERSION="1.1.0"
BRANCH="master"

# ============================================================
# 🛡️ AUTO-ESCLUSIONE DA GIT
# ============================================================

if [ ! -f ".gitignore" ]; then
    echo "github_update.sh" > .gitignore
    echo "✅ .gitignore creato"
else
    if ! grep -q "github_update.sh" .gitignore; then
        echo "github_update.sh" >> .gitignore
        echo "✅ github_update.sh aggiunto a .gitignore"
    fi
fi

# ============================================================
# 📊 STATO
# ============================================================

echo -e "${BLUE}📊 Stato attuale:${NC}"
git status --short

CHANGES=$(git status --porcelain | wc -l)
if [ $CHANGES -eq 0 ]; then
    echo -e "${YELLOW}⚠️  Nessun cambiamento${NC}"
else
    echo -e "${GREEN}📝 $CHANGES file modificati${NC}"
fi

# ============================================================
# 🔄 SINCORNIZZA
# ============================================================

echo ""
echo -e "${BLUE}🔄 Sincronizzazione...${NC}"
git pull origin $BRANCH --rebase 2>/dev/null || git pull origin $BRANCH

# ============================================================
# 📦 COMMIT
# ============================================================

echo ""
read -p "📝 Messaggio commit (Invio per default): " COMMIT_MSG
if [ -z "$COMMIT_MSG" ]; then
    COMMIT_MSG="Aggiornamento v$VERSION"
fi

git add .
git commit -m "$COMMIT_MSG"

# ============================================================
# 📤 PUSH
# ============================================================

echo ""
echo -e "${BLUE}📤 Push...${NC}"
git push origin $BRANCH

# ============================================================
# 🏷️ RELEASE
# ============================================================

echo ""
echo -e "${YELLOW}📦 Creare nuova release?${NC}"
echo "   ${GREEN}1) Nuova release${NC}"
echo "   ${YELLOW}2) Sovrascrivi esistente${NC}"
echo "   ${RED}3) Salta${NC}"
read -p "Scelta (1-3): " RELEASE_CHOICE

if [ "$RELEASE_CHOICE" == "1" ] || [ "$RELEASE_CHOICE" == "2" ]; then
    
    # Ottieni ultima versione
    LATEST_TAG=$(git tag -l "v*" | sort -V | tail -n 1)
    if [ -n "$LATEST_TAG" ]; then
        echo -e "${BLUE}📌 Ultima versione: $LATEST_TAG${NC}"
        BASE=${LATEST_TAG#v}
        IFS='.' read -r major minor patch <<< "$BASE"
        NEXT_VERSION="v$major.$minor.$((patch + 1))"
        echo -e "${GREEN}   Prossima: $NEXT_VERSION${NC}"
    fi
    
    read -p "📌 Versione (es. v1.0.0): " RELEASE_VERSION
    if [ -z "$RELEASE_VERSION" ]; then
        RELEASE_VERSION="$NEXT_VERSION"
    fi
    
    read -p "📝 Titolo release (Invio per default): " RELEASE_TITLE
    if [ -z "$RELEASE_TITLE" ]; then
        RELEASE_TITLE="RNID Manager $RELEASE_VERSION"
    fi
    
    read -p "📝 Note release (Invio per default): " RELEASE_NOTES
    if [ -z "$RELEASE_NOTES" ]; then
        RELEASE_NOTES="## 🚀 Novità
- Aggiornamenti e miglioramenti
- Bug fix"
    fi
    
    # Se sovrascrivi, elimina la vecchia
    if [ "$RELEASE_CHOICE" == "2" ]; then
        echo -e "${RED}🗑️  Eliminazione release esistente...${NC}"
        gh release delete "$RELEASE_VERSION" --yes 2>/dev/null
        git tag -d "$RELEASE_VERSION" 2>/dev/null
        git push origin ":refs/tags/$RELEASE_VERSION" 2>/dev/null
    fi
    
    # Crea tag e release
    echo -e "${BLUE}🏷️  Creazione tag...${NC}"
    git tag -a "$RELEASE_VERSION" -m "$RELEASE_TITLE"
    git push origin "$RELEASE_VERSION"
    
    echo -e "${BLUE}📦 Creazione release...${NC}"
    gh release create "$RELEASE_VERSION" \
        --title "$RELEASE_TITLE" \
        --notes "$RELEASE_NOTES"
    
    # Carica asset
    if [ -d "dist" ] && [ "$(ls -A dist 2>/dev/null)" ]; then
        echo ""
        echo -e "${BLUE}📂 Asset disponibili:${NC}"
        ls dist/
        echo ""
        read -p "📤 Caricare tutti gli asset? (y/n): " UPLOAD_ALL
        if [[ "$UPLOAD_ALL" == "y" || "$UPLOAD_ALL" == "Y" ]]; then
            cd dist
            for file in *; do
                if [ -f "$file" ]; then
                    echo -e "   ${GREEN}⬆️  $file${NC}"
                    gh release upload "$RELEASE_VERSION" "$file" --clobber
                fi
            done
            cd ..
            echo -e "${GREEN}✅ Asset caricati!${NC}"
        fi
    fi
    
    echo -e "${GREEN}✅ Release creata!${NC}"
    echo -e "${GREEN}🔗 https://github.com/$(git remote get-url origin | sed 's/.*://' | sed 's/\.git$//')/releases/tag/$RELEASE_VERSION${NC}"
fi

# ============================================================
# 🎯 FINE
# ============================================================

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  ✅ COMPLETATO!                                                 ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
echo -e "${GREEN}📁 Repository: $(git remote get-url origin | sed 's/.*://' | sed 's/\.git$//')${NC}"
echo -e "${GREEN}🌿 Branch: $BRANCH${NC}"
echo -e "${GREEN}📦 Release: https://github.com/$(git remote get-url origin | sed 's/.*://' | sed 's/\.git$//')/releases${NC}"