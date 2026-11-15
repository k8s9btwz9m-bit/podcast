#!/bin/bash
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
CARTELLA="$HOME/Podcast"

cd "$CARTELLA" || {
    echo "Non trovo la cartella $CARTELLA"
    read -n 1 -s -r -p "Premi un tasto per chiudere..."
    exit 1
}

echo "=============================================="
echo "  Aggiornamento podcast - $(date '+%d/%m/%Y %H:%M')"
echo "=============================================="
echo

python3 yt2podcast.py
ESITO=$?

echo
if [ $ESITO -eq 0 ]; then
    echo "OK - fatto. Puoi chiudere questa finestra."
elif [ $ESITO -eq 3 ]; then
    echo "Un aggiornamento e' gia' in corso. Riprova tra poco."
else
    echo "Lo script ha segnalato un problema (codice $ESITO). Apri il pannello"
    echo "per i dettagli."
fi
echo
read -n 1 -s -r -p "Premi un tasto per chiudere..."
