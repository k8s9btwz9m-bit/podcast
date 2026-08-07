#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
CARTELLA="$HOME/Documents/App/Podcast"

APPUNTI=$(pbpaste 2>/dev/null)
case "$APPUNTI" in
    *youtube.com*|*youtu.be*) PREIMPOSTATO="$APPUNTI" ;;
    *) PREIMPOSTATO="" ;;
esac

URL=$(osascript \
  -e 'on run {pre}' \
  -e 'text returned of (display dialog "Indirizzo del video YouTube:" default answer pre with title "Aggiungi al podcast YouTube" buttons {"Annulla", "Aggiungi"} default button "Aggiungi")' \
  -e 'end run' \
  "$PREIMPOSTATO" 2>/dev/null)

if [ -z "$URL" ]; then
    exit 0
fi

cd "$CARTELLA" || {
    echo "Non trovo la cartella $CARTELLA"
    read -n 1 -s -r -p "Premi un tasto per chiudere..."
    exit 1
}

echo "=============================================="
echo "  Aggiungo un video al podcast YouTube"
echo "=============================================="
echo

python3 yt2podcast.py --video "$URL" && python3 yt2podcast.py
ESITO=$?

echo
if [ $ESITO -eq 0 ]; then
    echo "OK - tra un paio di minuti lo trovi nell'app Podcast."
else
    echo "ERRORE (codice $ESITO)."
fi
echo
read -n 1 -s -r -p "Premi un tasto per chiudere..."
