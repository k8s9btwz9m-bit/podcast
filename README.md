# yt2podcast

Trasforma canali YouTube in feed podcast, pubblicati con GitHub Pages.

Sito: https://k8s9btwz9m-bit.github.io/podcast/

## Aggiornare (scarica novita' e pubblica)

    cd ~/Documents/podcast
    python3 yt2podcast.py

Fa tutto: legge i feed dei canali, scarica l'audio dei video nuovi,
rigenera gli XML, fa commit e push. Se il repository supera la soglia
di `max_repo_mb` azzera da solo la cronologia git.

## Aggiungere un canale

1. Ricava il channel ID (24 caratteri, inizia per UC):

       yt-dlp --print "%(channel_id)s" --playlist-items 1 "URL_DEL_CANALE"

2. Aggiungilo:

       python3 yt2podcast.py --add UCxxxxxxxxxxxxxxxxxxxxxx

   Ricava da solo nome, slug e descrizione dal feed del canale.
   Annota lo slug che stampa: serve al passo dopo.

3. Copertina. Salva l'immagine del profilo del canale, poi:

       ffmpeg -i ~/Downloads/immagine.jpg \
         -vf "scale=1400:1400:force_original_aspect_ratio=increase,crop=1400:1400" \
         -frames:v 1 -update 1 -y docs/cover-SLUG.jpg

   Il nome del file deve combaciare esattamente con lo slug.

4. Scarica e pubblica:

       python3 yt2podcast.py

5. Sul telefono: app Podcast > Libreria > "..." > Segui uno show
   tramite URL, e incolli l'indirizzo del feed. Tutti gli indirizzi
   sono elencati nella pagina del sito.

## Rimuovere un canale

Togli il suo blocco da `channels.json`, poi cancella
`state/SLUG.json`, `docs/SLUG.xml`, `docs/cover-SLUG.jpg` e la
cartella `docs/audio/SLUG/`. Infine rilancia lo script.

## Configurazione (channels.json)

- `max_episodes` — episodi tenuti per canale. Scaricare di piu' e'
  inutile: lo script scarica solo questi.
- `audio_quality` — 0 massima, 9 minima. 7 va bene per il parlato.
- `min_duration_minutes` / `max_duration_minutes` — esclude clip
  brevi e video troppo lunghi. Gli episodi gia' scaricati che
  sforano vengono rimossi al giro successivo.
- `max_repo_mb` — soglia oltre la quale la cronologia git viene
  azzerata. Il limite di GitHub Pages e' 1 GB.
- `keep_files` — se true non cancella mai nulla. Sconsigliato.
- `git_auto_push` — se false lo script non pubblica da solo.

## Se qualcosa non va

- Download che falliscono: aggiorna yt-dlp con `yt-dlp -U`.
- Copertina che non compare nell'app: togli e riaggiungi lo show,
  l'app tiene in cache la vecchia immagine.
- File oltre 100 MB: Git li rifiuta. Abbassa `max_duration_minutes`
  o alza `audio_quality`.
- Un video fallito finisce in `skipped` dentro `state/SLUG.json` e
  non viene piu' ritentato. Per riprovarci, togli il suo ID da li'.
