#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
CARTELLA="$HOME/Documents/App/Podcast"

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
else
    echo "ERRORE (codice $ESITO). Copia il messaggio qui sopra se ti serve aiuto."
fi
echo
read -n 1 -s -r -p "Premi un tasto per chiudere..."
