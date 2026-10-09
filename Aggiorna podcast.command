#!/bin/bash
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
CARTELLA="$HOME/Documents/App/Podcast"

cd "$CARTELLA" || {
    echo "Non trovo la cartella $CARTELLA"
    read -n 1 -s -r -p "Premi un tasto per chiudere..."
    exit 1
}

BLOCCO="$CARTELLA/.in-corso.lock"
if ! mkdir "$BLOCCO" 2>/dev/null; then
    ALTRO=$(cat "$BLOCCO/pid" 2>/dev/null)
    if [ -n "$ALTRO" ] && kill -0 "$ALTRO" 2>/dev/null; then
        echo "Un aggiornamento e' gia' in corso (pid $ALTRO). Esco."
        read -n 1 -s -r -p "Premi un tasto per chiudere..."
        exit 0
    fi
    echo "Trovato un blocco abbandonato: lo rimuovo."
    rm -rf "$BLOCCO"
    mkdir "$BLOCCO" 2>/dev/null || { echo "Non riesco a creare il blocco."; exit 1; }
fi
echo $$ > "$BLOCCO/pid"
trap 'rm -rf "$BLOCCO"' EXIT

GIA_PUBBLICATO=0

pubblica() {
    [ "$GIA_PUBBLICATO" = "1" ] && return 0
    GIA_PUBBLICATO=1
    echo
    echo "--- pubblicazione ---"
    git add -A
    if git diff --cached --quiet; then
        echo "niente di nuovo da pubblicare"
        return 0
    fi
    git commit -qm "aggiornamento $(date '+%Y-%m-%d %H:%M')"
    if git push -q; then
        echo "pubblicato su GitHub"
    else
        echo "PUSH FALLITO. Per capire cosa manca:"
        echo "  git fetch origin && git log --oneline HEAD..origin/main"
    fi
}

trap 'echo; echo "*** interrotto ***"' INT
trap pubblica EXIT

echo "=============================================="
echo "  Aggiornamento podcast - $(date '+%d/%m/%Y %H:%M')"
echo "=============================================="
echo

python3 yt2podcast.py
ESITO=$?

pubblica

echo
if [ $ESITO -eq 0 ]; then
    echo "OK - fatto. Puoi chiudere questa finestra."
else
    echo "Lo script ha segnalato un problema (codice $ESITO), ma quello"
    echo "che era gia' stato scaricato risulta pubblicato."
fi
echo
read -n 1 -s -r -p "Premi un tasto per chiudere..."
