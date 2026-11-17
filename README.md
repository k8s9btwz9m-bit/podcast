# yt2podcast

Trasforma canali e playlist YouTube in feed podcast, pubblicati con GitHub Pages.

Sito: https://k8s9btwz9m-bit.github.io/podcast/

## Il pannello

L'uso normale passa dal pannello: apri l'icona **Nazg** (in `~/Applications`,
trascinala nel Dock) oppure vai su http://127.0.0.1:8719/.

Dal pannello puoi:

- vedere se l'ultimo giro è riuscito e quando parte il prossimo;
- incollare il link di un canale, di una playlist o di un video: mostra
  un'anteprima di cosa entrerebbe, poi lo aggiunge;
- aggiornare subito, rimuovere uno show, cambiargli la copertina;
- dare regole a un singolo show (durata minima e massima, parole nei titoli,
  taglio degli sponsor);
- cambiare le impostazioni generali;
- ritentare un download fallito o scaricare tra i video singoli un video
  saltato;
- inquadrare il QR di uno show con l'iPhone per seguirlo nell'app Podcast.

Il pannello parte da solo all'accesso (`local.nazgul.pannello`) e risponde
solo dal Mac stesso. Se un giro fallisce arriva una notifica; arriva anche se
nessun giro riesce da più di 36 ore.

Il giro automatico parte ogni giorno alle 8:00 e alle 20:00
(`local.nazgul.aggiorna`).

## Questa cartella deve restare fuori da iCloud

Il progetto sta in `~/Claude/App/Nazgul/Rilasci/attuale`. Non spostarlo dentro `Documenti` o `Scrivania`:
iCloud toglie i file dal disco e gli aggiornamenti automatici smettono di
funzionare. In `Documenti/App` c'è solo un collegamento.

## Da Terminale

Tutto quello che fa il pannello si può fare anche a mano, da `~/Claude/App/Nazgul/Rilasci/attuale`:

    python3 yt2podcast.py                      # giro completo e pubblicazione
    python3 yt2podcast.py --only SLUG          # solo uno show
    python3 yt2podcast.py --preview "LINK"     # anteprima, stampa JSON
    python3 yt2podcast.py --add "LINK"         # canale, playlist o video
    python3 yt2podcast.py --video "LINK"       # video singolo nello show "YouTube"
    python3 yt2podcast.py --remove SLUG        # cancella show, audio e feed
    python3 yt2podcast.py --cover SLUG FILE    # nuova copertina
    python3 yt2podcast.py --aggiorna-programma # aggiorna yt-dlp, poi giro

Codici di uscita: 0 riuscito (anche con qualche problema), 1 errore del
comando, 2 giro fallito, 3 un altro aggiornamento è già in corso.

Gira un solo lavoro alla volta: chi arriva mentre un altro lavoro è in corso
aspetta fino a 30 minuti.

`Aggiorna podcast.command`, `Aggiungi video.command` e il comando rapido
(`da-shortcut.sh`) funzionano come prima.

## Cosa fa a ogni download

- Taglia sponsor, autopromozione e sigle segnalati da SponsorBlock.
- Livella il volume (-16 LUFS).
- Scrive i capitoli del video nell'mp3 e salva la miniatura come immagine
  dell'episodio.

Valgono per i download nuovi; si spengono dalle impostazioni.

## Playlist

Una playlist deve essere pubblica o "non in elenco". Lo show la rispecchia:
togliere un video dalla playlist lo toglie dal feed al giro successivo.
Massimo 50 episodi, nessun filtro di durata.

## Limiti

- GitHub rifiuta i file oltre 100 MB.
- Oltre 900 MB di sito pubblicato i download non partono.
- Oltre `max_repo_mb` (700) la cronologia git viene azzerata da sola.

## File e cartelle

- `channels.json` — impostazioni e show. Si modifica dal pannello.
- `state/` — episodi, saltati e falliti di ogni show.
- `docs/` — quello che viene pubblicato: feed, copertine, audio.
- `run/` — stato del lavoro in corso, ultimo giro, log dei lavori lanciati
  dal pannello (`lavori.log`) e del pannello (`pannello.log`). Non pubblicata.
- `progetto/` — progetto, piano e decisioni del pannello. Non pubblicata.
- `test/` — prove automatiche: `python3 -m unittest discover -s test`.

Non vengono pubblicati su GitHub: `run/`, `progetto/`, i log e i file di
blocco.

## Se qualcosa non va

- Il pannello dice cosa è successo e cosa fare. Per i dettagli:
  `run/lavori.log` e `log-automatico.txt`.
- YouTube rifiuta i download: dal pannello "Aggiorna il programma e riprova",
  oppure `uv tool upgrade yt-dlp`.
- Il pannello non si apre: `launchctl kickstart -k gui/$(id -u)/local.nazgul.pannello`.
- Copertina vecchia nell'app Podcast: togli e riaggiungi lo show, l'app tiene
  in cache l'immagine.
