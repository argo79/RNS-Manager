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
NC='\033[0m' # No Color

BRANCH="master"

# ============================================================
# 📖 LEGGI VERSIONE DA version.py
# ============================================================

if [ -f "version.py" ]; then
    CURRENT_VERSION=$(python3 -c "from version import APP_VERSION; print(APP_VERSION)" 2>/dev/null)
    if [ -z "$CURRENT_VERSION" ]; then
        CURRENT_VERSION="1.0.0"
    fi
else
    CURRENT_VERSION="1.0.0"
fi

echo -e "${BLUE}📌 Versione corrente: v${CURRENT_VERSION}${NC}"

# Calcola prossima versione (patch +1)
IFS='.' read -r major minor patch <<< "$CURRENT_VERSION"
NEXT_VERSION="v$major.$minor.$((patch + 1))"

# ============================================================
# 🛡️ AUTO-ESCLUSIONE DA GIT
# ============================================================

if [ ! -f ".gitignore" ]; then
    echo "github_update.sh" > .gitignore
    echo -e "${GREEN}✅ .gitignore creato${NC}"
else
    if ! grep -q "github_update.sh" .gitignore; then
        echo "github_update.sh" >> .gitignore
        echo -e "${GREEN}✅ github_update.sh aggiunto a .gitignore${NC}"
    fi
fi

# ============================================================
# 📊 STATO
# ============================================================

echo ""
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
    COMMIT_MSG="Aggiornamento v$CURRENT_VERSION"
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
echo -e "   ${GREEN}1) Nuova release${NC}"
echo -e "   ${YELLOW}2) Sovrascrivi esistente${NC}"
echo -e "   ${RED}3) Salta${NC}"
read -p "Scelta (1-3): " RELEASE_CHOICE

if [ "$RELEASE_CHOICE" == "1" ] || [ "$RELEASE_CHOICE" == "2" ]; then
    
    # Mostra versione attuale e precompila
    echo ""
    echo -e "${BLUE}📌 Versione attuale: v${CURRENT_VERSION}${NC}"
    echo -e "${GREEN}   Prossima: $NEXT_VERSION${NC}"
    echo ""
    
    read -p "📌 Versione (es. v1.0.0) [default: $NEXT_VERSION]: " RELEASE_VERSION
    if [ -z "$RELEASE_VERSION" ]; then
        RELEASE_VERSION="$NEXT_VERSION"
    fi
    
    # Precompila titolo con versione
    DEFAULT_TITLE="RNID Manager $RELEASE_VERSION"
    read -p "📝 Titolo release [default: $DEFAULT_TITLE]: " RELEASE_TITLE
    if [ -z "$RELEASE_TITLE" ]; then
        RELEASE_TITLE="$DEFAULT_TITLE"
    fi
    
    # Precompila note con changelog
    DEFAULT_NOTES="## 🚀 Novità v$RELEASE_VERSION

### ✨ Aggiunte
- 

### 🐛 Bug Fix
- 

### 🔧 Miglioramenti
- 

### 📦 Dipendenze
- Aggiornate dipendenze

---"
    echo ""
    echo -e "${BLUE}📝 Note release (modifica se necessario):${NC}"
    echo -e "${YELLOW}   (premi Invio per usare il template)${NC}"
    echo ""
    read -p "📝 Note release [default: template automatico]: " RELEASE_NOTES
    if [ -z "$RELEASE_NOTES" ]; then
        RELEASE_NOTES="$DEFAULT_NOTES"
    fi
    
    # Se sovrascrivi, elimina la vecchia
    if [ "$RELEASE_CHOICE" == "2" ]; then
        echo ""
        echo -e "${RED}🗑️  Eliminazione release esistente...${NC}"
        gh release delete "$RELEASE_VERSION" --yes 2>/dev/null
        git tag -d "$RELEASE_VERSION" 2>/dev/null
        git push origin ":refs/tags/$RELEASE_VERSION" 2>/dev/null
        echo -e "${GREEN}✅ Release eliminata${NC}"
    fi
    
    # Crea tag e release
    echo ""
    echo -e "${BLUE}🏷️  Creazione tag...${NC}"
    git tag -a "$RELEASE_VERSION" -m "$RELEASE_TITLE"
    git push origin "$RELEASE_VERSION"
    echo -e "${GREEN}✅ Tag creato${NC}"
    
    echo ""
    echo -e "${BLUE}📦 Creazione release...${NC}"
    gh release create "$RELEASE_VERSION" \
        --title "$RELEASE_TITLE" \
        --notes "$RELEASE_NOTES"
    
    # Carica asset
    if [ -d "dist" ] && [ "$(ls -A dist 2>/dev/null)" ]; then
        echo ""
        echo -e "${BLUE}📂 Asset disponibili in dist/:${NC}"
        ls -la dist/
        
        # Cerca il file .run
        RUN_FILE=$(ls dist/*.run 2>/dev/null | head -n 1)
        
        echo ""
        echo -e "${YELLOW}📤 Caricare asset?${NC}"
        echo -e "   ${GREEN}1) Carica tutti${NC}"
        echo -e "   ${GREEN}2) Carica solo .run${NC}"
        echo -e "   ${GREEN}3) Seleziona manualmente${NC}"
        echo -e "   ${RED}4) Salta${NC}"
        read -p "Scelta (1-4): " UPLOAD_CHOICE
        
        case $UPLOAD_CHOICE in
            1)
                echo -e "${BLUE}📤 Caricamento tutti gli asset...${NC}"
                cd dist
                for file in *; do
                    if [ -f "$file" ]; then
                        echo -e "   ${GREEN}⬆️  $file${NC}"
                        gh release upload "$RELEASE_VERSION" "$file" --clobber
                    fi
                done
                cd ..
                echo -e "${GREEN}✅ Tutti gli asset caricati!${NC}"
                ;;
            2)
                if [ -n "$RUN_FILE" ]; then
                    echo -e "${BLUE}📤 Caricamento .run...${NC}"
                    gh release upload "$RELEASE_VERSION" "$RUN_FILE" --clobber
                    echo -e "${GREEN}✅ $RUN_FILE caricato!${NC}"
                else
                    echo -e "${RED}❌ Nessun file .run trovato!${NC}"
                fi
                ;;
            3)
                echo ""
                echo -e "${BLUE}📂 Seleziona file da caricare:${NC}"
                cd dist
                select file in *; do
                    if [ -n "$file" ]; then
                        echo -e "   ${GREEN}⬆️  $file${NC}"
                        gh release upload "$RELEASE_VERSION" "$file" --clobber
                        echo -e "${GREEN}✅ $file caricato!${NC}"
                        break
                    fi
                done
                cd ..
                ;;
            *)
                echo -e "${YELLOW}⏭️  Upload saltato${NC}"
                ;;
        esac
    fi
    
    echo ""
    echo -e "${GREEN}✅ Release creata!${NC}"
    REPO_URL=$(git remote get-url origin | sed 's/.*://' | sed 's/\.git$//')
    echo -e "${GREEN}🔗 https://github.com/$REPO_URL/releases/tag/$RELEASE_VERSION${NC}"
fi

# ============================================================
# 🎯 FINE
# ============================================================

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  ✅ COMPLETATO!                                                 ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
REPO_URL=$(git remote get-url origin | sed 's/.*://' | sed 's/\.git$//')
echo -e "${GREEN}📁 Repository: https://github.com/$REPO_URL${NC}"
echo -e "${GREEN}🌿 Branch: $BRANCH${NC}"
echo -e "${GREEN}📦 Release: https://github.com/$REPO_URL/releases${NC}"
echo -e "${GREEN}📌 Versione: v$CURRENT_VERSION${NC}"
echo ""