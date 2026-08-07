#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
CARTELLA="$(cd "$(dirname "$0")" && pwd)"
LOG="$CARTELLA/log-shortcut.txt"

avvisa() {
    osascript -e "on run {m}" -e "display notification m with title \"Podcast\"" \
              -e "end run" "$1" 2>/dev/null
}

URL="$1"
if [ -z "$URL" ]; then
    avvisa "Nessun indirizzo ricevuto."
    exit 1
fi

cd "$CARTELLA" || { avvisa "Cartella podcast non trovata."; exit 1; }

{
    echo "===== $(date '+%Y-%m-%d %H:%M') - $URL"
    python3 yt2podcast.py --video "$URL" && python3 yt2podcast.py
} >> "$LOG" 2>&1
ESITO=$?

if [ $ESITO -eq 0 ]; then
    avvisa "Video aggiunto e pubblicato."
else
    avvisa "Non ha funzionato. Guarda log-shortcut.txt"
fi
exit $ESITO
