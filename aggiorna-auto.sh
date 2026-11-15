#!/bin/bash
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
CARTELLA="$(cd "$(dirname "$0")" && pwd)"
cd "$CARTELLA" || exit 1

# blocco, pubblicazione e notifiche li gestisce yt2podcast.py
echo "===== $(date '+%Y-%m-%d %H:%M') ====="
python3 yt2podcast.py
ESITO=$?
echo "===== fine ====="
exit $ESITO
