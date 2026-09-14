#!/bin/bash
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
CARTELLA="$(cd "$(dirname "$0")" && pwd)"
cd "$CARTELLA" || exit 1

BLOCCO="$CARTELLA/.in-corso.lock"
if ! mkdir "$BLOCCO" 2>/dev/null; then
    ALTRO=$(cat "$BLOCCO/pid" 2>/dev/null)
    if [ -n "$ALTRO" ] && kill -0 "$ALTRO" 2>/dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M') - aggiornamento gia' in corso (pid $ALTRO), salto"
        exit 0
    fi
    rm -rf "$BLOCCO"
    mkdir "$BLOCCO" 2>/dev/null || exit 1
fi
echo $$ > "$BLOCCO/pid"
trap 'rm -rf "$BLOCCO"' EXIT

echo "===== $(date '+%Y-%m-%d %H:%M') ====="
python3 yt2podcast.py
git add -A
if ! git diff --cached --quiet; then
    git commit -qm "aggiornamento automatico $(date '+%Y-%m-%d %H:%M')"
    git push -q && echo "pubblicato" || echo "PUSH FALLITO"
fi
echo "===== fine ====="
