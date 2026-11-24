#!/bin/bash
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
CARTELLA="$(cd "$(dirname "$0")" && pwd)"
cd "$CARTELLA" || exit 1

# Il servizio parte alle 8:00, alle 20:00 e anche all'accensione o al login, per recuperare
# un giro saltato a Mac spento. Se l'ultimo giro automatico e' partito da meno di 2 ore,
# questo avvio non serve: si salta (cosi' un riavvio non rifa' un giro appena fatto).
STAMPO="$CARTELLA/state/ultimo-avvio-auto"
ORA=$(date +%s)
if [ -f "$STAMPO" ]; then
    PRIMA=$(cat "$STAMPO" 2>/dev/null)
    case "$PRIMA" in
        ''|*[!0-9]*) PRIMA=0 ;;
    esac
    if [ $((ORA - PRIMA)) -lt 7200 ]; then
        echo "===== $(date '+%Y-%m-%d %H:%M') · salto: ultimo giro automatico meno di 2 ore fa ====="
        exit 0
    fi
fi
mkdir -p "$CARTELLA/state"
echo "$ORA" > "$STAMPO"

# blocco, pubblicazione e notifiche li gestisce yt2podcast.py
echo "===== $(date '+%Y-%m-%d %H:%M') ====="
python3 yt2podcast.py
ESITO=$?
echo "===== fine ====="
exit $ESITO
