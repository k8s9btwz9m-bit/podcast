#!/usr/bin/env python3
"""
yt2podcast.py — da canale YouTube a feed podcast.  (versione 4)

USO NORMALE (scarica novita', rigenera i feed, pubblica su GitHub):
    python3 yt2podcast.py

AGGIUNGERE UN CANALE, UNA PLAYLIST O UN VIDEO (basta incollare il link):
    python3 yt2podcast.py --add LINK
    python3 yt2podcast.py --preview LINK        (solo JSON: cosa entrerebbe)
    python3 yt2podcast.py --remove SLUG
    python3 yt2podcast.py --cover SLUG FILE_IMMAGINE
    python3 yt2podcast.py --only SLUG           (giro su un solo show)
    python3 yt2podcast.py --aggiorna-programma  (aggiorna yt-dlp, poi il giro)
    opzioni: --senza-attesa (non aspetta un altro giro), --regole JSON

AGGIUNGERE UN SINGOLO VIDEO al podcast "YouTube":
    python3 yt2podcast.py --video "https://www.youtube.com/watch?v=..."

Dipendenze esterne: yt-dlp, ffmpeg (ffprobe), git.
Solo libreria standard Python: nessun pip install aggiuntivo.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

import regole as regole_mod
import stato_giro

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "channels.json")
STATE_DIR = os.path.join(HERE, "state")
DOCS_DIR = os.path.join(HERE, "docs")
AUDIO_ROOT = os.path.join(DOCS_DIR, "audio")
GIT_DIR = os.path.join(HERE, ".git")

MAX_TENTATIVI = 3
LIMITE_SITO_MB = 900
MOTIVO_SPAZIO = f"spazio esaurito: sito oltre {LIMITE_SITO_MB} MB"
TIMEOUT_YTDLP = 120
MOTIVO_ATTESA = "non ancora disponibile (diretta o première programmata)"
MODELLO_ATTESA = r"will begin in|premieres in|live event"


class Errore(Exception):
    """Errore da mostrare all'utente: main scrive il messaggio ed esce con 1."""


def imposta_radice(radice):
    """Fa puntare tutti i percorsi alla cartella data (serve ai test)."""
    global HERE, CONFIG_FILE, STATE_DIR, DOCS_DIR, AUDIO_ROOT, GIT_DIR
    HERE = os.path.abspath(radice)
    CONFIG_FILE = os.path.join(HERE, "channels.json")
    STATE_DIR = os.path.join(HERE, "state")
    DOCS_DIR = os.path.join(HERE, "docs")
    AUDIO_ROOT = os.path.join(DOCS_DIR, "audio")
    GIT_DIR = os.path.join(HERE, ".git")

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


# ---------------------------------------------------------------- utility

def load_json(path, default=None):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # scrittura atomica: chi legge vede il file vecchio o quello nuovo, mai a meta'
    fd, tmp = tempfile.mkstemp(dir=parent or ".", prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def log(msg):
    print(msg, flush=True)


def hhmmss(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def human_mb(num_bytes):
    return num_bytes / 1024 / 1024


def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "canale"


def check_requirements():
    missing = []
    for exe in ("yt-dlp", "ffprobe", "git"):
        try:
            subprocess.run([exe, "--version"], capture_output=True, timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            missing.append(exe)
    if missing:
        raise Errore("ERRORE: programmi mancanti: " + ", ".join(missing))


def git(*args, check=True):
    """Esegue un comando git nella cartella del progetto."""
    result = subprocess.run(["git", *args], cwd=HERE,
                            capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} fallito:\n{result.stderr.strip()}")
    return result.stdout.strip()


# ---------------------------------------------------------------- YouTube

def playlist_url(playlist_id):
    return f"https://www.youtube.com/playlist?list={playlist_id}"


def feed_url_for(channel_id):
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def fetch_channel_feed(channel_id):
    """Scarica il feed RSS pubblico del canale. Restituisce (titolo, video)."""
    req = urllib.request.Request(feed_url_for(channel_id),
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return parse_channel_feed(raw)


def parse_channel_feed(raw):
    root = ET.fromstring(raw)
    ch_title_el = root.find("atom:title", NS)
    channel_title = (ch_title_el.text or "").strip() if ch_title_el is not None else ""

    videos = []
    for entry in root.findall("atom:entry", NS):
        vid_el = entry.find("yt:videoId", NS)
        if vid_el is None or not vid_el.text:
            continue
        title_el = entry.find("atom:title", NS)
        pub_el = entry.find("atom:published", NS)
        group = entry.find("media:group", NS)
        desc = ""
        if group is not None:
            d = group.find("media:description", NS)
            if d is not None and d.text:
                desc = d.text.strip()
        videos.append({
            "id": vid_el.text.strip(),
            "title": (title_el.text or vid_el.text).strip(),
            "published": (pub_el.text or "").strip(),
            "description": desc,
        })
    return channel_title, videos


def elenco_ytdlp(url, limite):
    """Elenco dei video di un canale o di una playlist, senza scaricarli.

    Unico punto che chiama `yt-dlp --flat-playlist`. limite 0 = tutta la lista.
    Ogni voce: {"id", "title", "duration": int | None, "published", "description"}."""
    # senza raise_incomplete_data, "Incomplete data received" sulle pagine
    # successive e' solo un avviso (nascosto da --no-warnings) e l'elenco
    # arriva tagliato con codice 0
    cmd = ["yt-dlp", "--flat-playlist", "--dump-json", "--no-warnings",
           "--extractor-args", "youtube:raise_incomplete_data"]
    if limite:
        cmd += ["--playlist-end", str(limite)]
    try:
        r = subprocess.run(cmd + [url], capture_output=True, text=True,
                           timeout=TIMEOUT_YTDLP)
    except subprocess.TimeoutExpired:
        raise Errore("ERRORE: yt-dlp non ha risposto in tempo (timed out).")
    if r.returncode != 0:
        righe = (r.stderr or "").strip().splitlines()
        raise Errore("ERRORE: yt-dlp non riesce a leggere l'elenco dei video."
                     + (f"\n{righe[-1]}" if righe else ""))
    voci = []
    for riga in r.stdout.splitlines():
        try:
            d = json.loads(riga)
        except json.JSONDecodeError:
            continue
        if not d.get("id"):
            continue
        pub = ""
        if d.get("timestamp"):
            pub = datetime.fromtimestamp(d["timestamp"], timezone.utc).isoformat()
        durata = d.get("duration")
        voci.append({
            "id": d["id"],
            "title": (d.get("title") or d["id"]).strip(),
            "duration": int(durata) if durata else None,
            "published": pub,
            "description": (d.get("description") or "").strip(),
        })
    return voci


def ytdlp_json(args):
    """Metadati di una sorgente (un solo oggetto JSON) tramite yt-dlp."""
    try:
        r = subprocess.run(
            ["yt-dlp", "--dump-single-json", "--no-warnings", *args],
            capture_output=True, text=True, timeout=TIMEOUT_YTDLP)
    except subprocess.TimeoutExpired:
        raise Errore("ERRORE: yt-dlp non ha risposto in tempo (timed out).")
    if r.returncode != 0 or not r.stdout.strip():
        tail = (r.stderr or "").strip()[-400:]
        raise Errore(f"ERRORE: yt-dlp non riesce a leggere il link.\n{tail}")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        raise Errore("ERRORE: risposta di yt-dlp non valida.")


def fetch_channel_via_ytdlp(channel_id, limit):
    """Elenco dei video recenti senza passare dal feed RSS (che a volte da' 404)."""
    return elenco_ytdlp(f"https://www.youtube.com/channel/{channel_id}/videos",
                        limit)


def build_match_filter(cfg, regole=None):
    """Filtro yt-dlp: niente dirette, niente clip brevi, niente video lunghissimi."""
    parts = ["!is_live"]
    if regole is not None:
        min_s, max_s = regole["min_s"], regole["max_s"]
    else:
        min_s = int(cfg.get("min_duration_minutes", 2)) * 60
        max_s = int(cfg.get("max_duration_minutes", 120)) * 60
    if min_s > 0:
        parts.append(f"duration > {min_s}")
    if max_s > 0:
        parts.append(f"duration < {max_s}")
    return " & ".join(parts)


SPONSOR_CATEGORIE = "sponsor,selfpromo,interaction,intro,outro"
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"
LATO_IMMAGINE = 720
LATO_COPERTINA = 1400


def comando_download(video_id, dest_dir, cfg, regole, sponsor):
    """Comando yt-dlp per un episodio: sponsor, capitoli, volume, miniatura."""
    cmd = [
        "yt-dlp",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", str(cfg.get("audio_quality", "7")),
        "--embed-thumbnail",
        # tiene anche ID.jpg accanto all'mp3: diventa l'immagine dell'episodio
        "--write-thumbnail", "--convert-thumbnails", "jpg",
        "--embed-metadata",
        "--no-playlist",
        "--retries", "3",
        "--no-progress",
        # stampa la durata originale (prima dei tagli) a download finito.
        # --print attiva da solo la modalita' silenziosa, e li' yt-dlp non
        # dice piu' "does not pass filter": --no-quiet la rimette com'era
        "--no-simulate", "--print", "after_move:%(duration)s", "--no-quiet",
        # solo tracce audio: mai scaricare il video per poi estrarne l'audio
        "-f", "bestaudio",
        "--remote-components", "ejs:github",
        "--match-filter", build_match_filter(cfg, regole),
        *(["--extractor-args",
           "youtube:player_client=" + cfg["player_client"]]
          if cfg.get("player_client") else []),
    ]
    if sponsor:
        cmd += ["--sponsorblock-remove", SPONSOR_CATEGORIE]
    if cfg.get("chapters", True):
        cmd += ["--embed-chapters"]
    args = []
    if cfg.get("mono", False):
        # una voce non guadagna nulla dallo stereo: dimezza il peso
        args.append("-ac 1")
    if cfg.get("loudnorm", True):
        args.append("-af " + LOUDNORM)
    if args:
        # solo sull'estrazione audio: un filtro non vale con la copia della miniatura
        cmd += ["--postprocessor-args", "ExtractAudio:" + " ".join(args)]
    cmd += [
        "-o", os.path.join(dest_dir, "%(id)s.%(ext)s"),
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    return cmd


def _durata_stampata(stdout):
    """Ultima riga numerica dell'output di yt-dlp (la durata originale), o 0."""
    for riga in reversed((stdout or "").splitlines()):
        try:
            return int(float(riga.strip()))
        except ValueError:
            continue
    return 0


def conta_capitoli(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_chapters", path],
            capture_output=True, text=True, timeout=60)
        return out.stdout.count("[CHAPTER]")
    except Exception:
        return 0


def quadra_immagine(path, lato=LATO_IMMAGINE):
    """Ritaglia l'immagine a quadrato centrato lato x lato, sul posto. True se riuscito."""
    tmp = path[:-4] + ".quadrata.jpg"
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", path, "-vf",
             f"crop='min(iw,ih)':'min(iw,ih)',scale={lato}:{lato}",
             "-frames:v", "1", tmp],
            capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not os.path.exists(tmp):
            return False
        os.replace(tmp, path)
        return True
    except Exception:
        return False
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def rimuovi_episodio(audio_dir, ep):
    """Cancella i file di un episodio: l'mp3 e, se c'e', la sua immagine."""
    for chiave in ("file", "image"):
        nome = ep.get(chiave)
        if nome:
            p = os.path.join(audio_dir, nome)
            if os.path.exists(p):
                os.remove(p)


def download_audio(video_id, dest_dir, cfg, regole=None):
    """Scarica l'audio come MP3. Restituisce (info, motivo, tipo).

    info: {"file", "image", "chapters", "tagliati_s"}.
    tipo: "" se riuscito, "regola" se il filtro di yt-dlp lo scarta,
    "attesa" se il video non e' ancora disponibile (diretta o prima
    programmata), "fallito" in ogni altro caso."""
    os.makedirs(dest_dir, exist_ok=True)
    sponsor = (regole["sponsor"] if regole is not None
               else cfg.get("sponsorblock", True))
    path = os.path.join(dest_dir, f"{video_id}.mp3")
    log(f"    scarico {video_id} ...")
    while True:
        cmd = comando_download(video_id, dest_dir, cfg, regole, sponsor)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if (not os.path.exists(path) and sponsor
                and "SponsorBlock" in (result.stderr or "")):
            log("    SponsorBlock non risponde: riprovo senza tagli")
            sponsor = False
            continue
        break

    if not os.path.exists(path):
        tail = (result.stderr or result.stdout or "").strip()[-500:]
        motivo = tail.splitlines()[-1] if tail else "nessun file prodotto"
        completo = (result.stderr or "") + (result.stdout or "")
        in_attesa = re.search(MODELLO_ATTESA, completo, re.IGNORECASE)
        fuori_filtro = "does not pass filter" in completo
        log(f"    saltato {video_id} ({motivo})")
        # ripulisce eventuali residui (miniature, file parziali)
        for leftover in os.listdir(dest_dir):
            if leftover.startswith(video_id) and not leftover.endswith(".mp3"):
                os.remove(os.path.join(dest_dir, leftover))
        if in_attesa:
            return None, MOTIVO_ATTESA, "attesa"
        if fuori_filtro:
            return None, "fuori dai limiti di durata", "regola"
        return None, motivo, "fallito"

    originale = _durata_stampata(result.stdout)
    reale = get_duration(path) if sponsor and originale else 0
    # con ffprobe fallito (durata 0) non si sa quanto e' stato tagliato
    tagliati = max(0, originale - reale) if reale else 0
    jpg = os.path.join(dest_dir, f"{video_id}.jpg")
    image = None
    if os.path.exists(jpg):
        if quadra_immagine(jpg):
            image = f"{video_id}.jpg"
        else:
            os.remove(jpg)
    return {"file": f"{video_id}.mp3", "image": image,
            "chapters": conta_capitoli(path) if cfg.get("chapters", True) else 0,
            "tagliati_s": tagliati}, "", ""


def get_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=60,
        )
        return int(float(out.stdout.strip()))
    except Exception:
        return 0


# ---------------------------------------------------------------- feed RSS

def rfc822(iso_string):
    try:
        dt = datetime.fromisoformat((iso_string or "").replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return format_datetime(dt)


def origine_url(channel):
    """Pagina YouTube da cui nasce lo show."""
    if channel.get("type") == "playlist":
        return playlist_url(channel["playlist_id"])
    return f"https://www.youtube.com/channel/{channel.get('id', '')}"


def build_feed(cfg, channel, episodes):
    site = cfg["site_url"].rstrip("/")
    slug = channel["slug"]
    feed_url = f"{site}/{slug}.xml"
    cover_url = channel.get("cover") or f"{site}/cover.jpg"
    author = channel.get("author") or cfg.get("author", "")
    max_ep = int(channel.get("max_episodes", cfg.get("max_episodes", 40)))
    desc_ch = channel.get("description", channel["title"])

    link = origine_url(channel)
    items = []
    for ep in episodes[:max_ep]:
        path = os.path.join(AUDIO_ROOT, slug, ep["file"])
        if not os.path.exists(path):
            continue
        size = os.path.getsize(path)
        audio_url = f"{site}/audio/{slug}/{ep['file']}"
        desc = ep.get("description", "")[:3000]
        immagine = ""
        if ep.get("image") and os.path.exists(
                os.path.join(AUDIO_ROOT, slug, ep["image"])):
            img_url = f"{site}/audio/{slug}/{ep['image']}"
            immagine = f"      <itunes:image href={quoteattr(img_url)}/>\n"
        items.append(
            "    <item>\n"
            f"      <title>{escape(ep['title'])}</title>\n"
            f"      <description>{escape(desc)}</description>\n"
            f"      <itunes:summary>{escape(desc)}</itunes:summary>\n"
            f"      <pubDate>{rfc822(ep['published'])}</pubDate>\n"
            f"      <guid isPermaLink=\"false\">yt-{ep['id']}</guid>\n"
            f"      <link>https://www.youtube.com/watch?v={ep['id']}</link>\n"
            f"      <enclosure url={quoteattr(audio_url)} length=\"{size}\" type=\"audio/mpeg\"/>\n"
            f"      <itunes:duration>{hhmmss(ep.get('duration'))}</itunes:duration>\n"
            f"{immagine}"
            "      <itunes:episodeType>full</itunes:episodeType>\n"
            "      <itunes:explicit>false</itunes:explicit>\n"
            "    </item>"
        )

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(channel['title'])}</title>
    <description>{escape(desc_ch)}</description>
    <link>{escape(link)}</link>
    <language>{escape(channel.get('language', cfg.get('language', 'it')))}</language>
    <lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>
    <atom:link href={quoteattr(feed_url)} rel="self" type="application/rss+xml"/>
    <image>
      <url>{escape(cover_url)}</url>
      <title>{escape(channel['title'])}</title>
      <link>{escape(link)}</link>
    </image>
    <itunes:author>{escape(author)}</itunes:author>
    <itunes:summary>{escape(desc_ch)}</itunes:summary>
    <itunes:image href={quoteattr(cover_url)}/>
    <itunes:category text={quoteattr(channel.get('category', 'Technology'))}/>
    <itunes:explicit>false</itunes:explicit>
    <itunes:type>episodic</itunes:type>
{chr(10).join(items)}
  </channel>
</rss>
"""
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(os.path.join(DOCS_DIR, f"{slug}.xml"), "w", encoding="utf-8") as f:
        f.write(xml)
    log(f"    feed: docs/{slug}.xml ({len(items)} episodi)")
    return feed_url, len(items)


def _link_podcast(feed_url):
    """Indirizzo che apre direttamente l'app Podcast (schema podcast://)."""
    return re.sub(r"^https?://", "podcast://", feed_url)


def build_index(cfg, show):
    """Pagina indice. `show`: lista di dict con title, feed_url, count."""
    lis = "\n".join(
        f'    <li><strong>{escape(s["title"])}</strong> — {s["count"]} episodi<br>'
        f'<code>{escape(s["feed_url"])}</code><br>'
        f'<a href={quoteattr(_link_podcast(s["feed_url"]))}>Apri in Podcast</a></li>'
        for s in show
    )
    html = f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>I miei feed podcast</title>
<style>
 body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 42rem;
        margin: 3rem auto; padding: 0 1rem; line-height: 1.6; }}
 code {{ background: #f4f4f4; padding: .15rem .35rem; border-radius: 4px;
         font-size: .85rem; word-break: break-all; }}
 li {{ margin-bottom: 1.2rem; }}
</style>
</head>
<body>
  <h1>I miei feed podcast</h1>
  <p>Copia uno di questi indirizzi e incollalo nell'app Podcast:
     Libreria &rarr; &laquo;&hellip;&raquo; &rarr; <em>Segui uno show tramite URL</em>.</p>
  <ul>
{lis}
  </ul>
  <p style="color:#666;font-size:.85rem">Aggiornato il {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
</body>
</html>
"""
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


# ------------------------------------------------------- manutenzione git

def git_dir_size():
    """Peso su disco della cartella .git, in byte."""
    total = 0
    for root, _dirs, files in os.walk(GIT_DIR):
        for name in files:
            p = os.path.join(root, name)
            if not os.path.islink(p):
                try:
                    total += os.path.getsize(p)
                except OSError:
                    pass
    return total


def git_identity():
    """Nome ed email dei commit, da riapplicare dopo aver cancellato .git."""
    out = {}
    for key in ("user.name", "user.email"):
        try:
            out[key] = git("config", "--get", key)
        except RuntimeError:
            out[key] = ""
    return out


def git_reset_history(remote_url, branch, identity):
    """Azzera la cronologia: il repository torna a pesare quanto i file attuali."""
    log("    azzero la cronologia git ...")
    shutil.rmtree(GIT_DIR)
    git("init", "-b", branch)
    # subito: se il resto si ferma a meta', .git non resta mai senza origin
    git("remote", "add", "origin", remote_url)
    # se l'identita' era impostata solo nel repository, si e' persa con .git
    for key, value in identity.items():
        if value:
            git("config", key, value)
    git("add", "-A")
    git("commit", "-m",
        f"reset cronologia {datetime.now().strftime('%Y-%m-%d')}")
    git("push", "-f", "-u", "origin", branch)
    log(f"    cronologia azzerata: ora {human_mb(git_dir_size()):.0f} MB")


MSG_NON_COLLEGATA = "La cartella non è collegata a GitHub: non posso pubblicare."


def git_publish(cfg):
    """Commit + push. Se il repository e' troppo grande, azzera la cronologia.

    Solleva Errore se commit o push falliscono, o se la cartella non e'
    collegata a GitHub (il chiamante pubblica solo con git_auto_push attivo)."""
    if not os.path.isdir(GIT_DIR):
        log("\nNessun repository git qui: non posso pubblicare.")
        raise Errore(MSG_NON_COLLEGATA)

    try:
        remote_url = git("remote", "get-url", "origin")
    except RuntimeError:
        log("\nNessun remote 'origin' configurato: non posso pubblicare.")
        raise Errore(MSG_NON_COLLEGATA)

    try:
        # symbolic-ref funziona anche su un repository senza commit
        try:
            branch = git("symbolic-ref", "--short", "HEAD") or "main"
        except RuntimeError:
            branch = "main"

        identity = git_identity()

        log("\nPubblicazione su GitHub")
        git("add", "-A")
        if git("status", "--porcelain"):
            git("commit", "-m",
                f"aggiornamento {datetime.now().strftime('%Y-%m-%d %H:%M')}")
            log("    commit creato")
        else:
            log("    niente di nuovo da committare")

        size_mb = human_mb(git_dir_size())
        limit_mb = float(cfg.get("max_repo_mb", 800))
        log(f"    repository: {size_mb:.0f} MB (limite impostato: {limit_mb:.0f} MB)")

        if size_mb > limit_mb:
            log("    ATTENZIONE: superata la soglia, la cronologia va azzerata")
            git_reset_history(remote_url, branch, identity)
            return

        git("push", "-u", "origin", branch)
        log("    push completato")
    except RuntimeError as e:
        log(f"    PUBBLICAZIONE FALLITA:\n{e}")
        log("    I file in locale sono a posto. Se il rifiuto e' 'fetch first',")
        log("    guarda cosa manca con:  git log --oneline HEAD..origin/main")
        raise Errore(f"git push/commit: {e}")


# ---------------------------------------------------------------- canali

def _ora():
    return datetime.now(timezone.utc).isoformat()


def _esito(channel, feed_url, count, **extra):
    r = {"title": channel["title"], "feed_url": feed_url, "count": count,
         "slug": channel["slug"], "nuovi": 0, "saltati": 0, "rimossi": 0,
         "falliti": 0, "tentati": 0, "saltati_info": [],
         "elenco_fallito": False, "errore_show": False}
    r.update(extra)
    return r


def _carica_stato(state_file):
    state = load_json(state_file, None) or {}
    state.setdefault("episodes", [])
    state.setdefault("skipped_info", {})
    state.setdefault("falliti", {})
    return state


def peso_sito_mb():
    """Peso del sito pubblicato (cartella docs), in megabyte interi."""
    totale = 0
    for radice, _dirs, files in os.walk(DOCS_DIR):
        for nome in files:
            p = os.path.join(radice, nome)
            if not os.path.islink(p):
                try:
                    totale += os.path.getsize(p)
                except OSError:
                    pass
    return int(human_mb(totale))


_avanzamento = None   # contesto del giro in corso; None fuori da un giro


def _avanza(**campi):
    """Aggiorna e scrive l'avanzamento del giro; non fa nulla fuori da un giro."""
    if _avanzamento is None:
        return
    _avanzamento.update(campi)
    stato_giro.scrivi_avanzamento(HERE, dict(_avanzamento))


def _avanza_video(v, n, tot):
    _avanza(video_n=n, video_tot=tot,
            testo=f"Scarico: {v['title'][:60]} ({n} di {tot})")


def _rifiuta_per_spazio(slug, v, esito, falliti):
    """True se il sito e' pieno: il download non parte e il video va in falliti.

    I tentativi non salgono: l'episodio non diventa mai un salto definitivo."""
    if peso_sito_mb() < LIMITE_SITO_MB:
        return False
    log(f"    sito pieno: non scarico {v['id']}")
    esito["tentati"] += 1
    esito["falliti"] += 1
    falliti[v["id"]] = {"title": v["title"], "reason": MOTIVO_SPAZIO,
                        "tentativi": falliti.get(v["id"], {}).get("tentativi", 0),
                        "date": _ora()}
    esito["saltati_info"].append({"slug": slug, "id": v["id"],
                                  "title": v["title"], "reason": MOTIVO_SPAZIO,
                                  "kind": "fallito"})
    return True


def _scarica_nuovo(slug, v, audio_dir, cfg, regole, esito, falliti, salta):
    """Un download di un video nuovo, con tutta la contabilita' dell'esito.

    Unico punto per spazio esaurito, durata fuori dai limiti, video non ancora
    disponibile e fallimenti con i loro tentativi. Restituisce `info` se il
    file c'e', altrimenti None (il video e' gia' stato registrato)."""
    if _rifiuta_per_spazio(slug, v, esito, falliti):
        return None
    esito["tentati"] += 1
    info, motivo, tipo = download_audio(v["id"], audio_dir, cfg, regole)
    if tipo == "regola":
        salta(v, "fuori dai limiti di durata", "regola")
        esito["saltati"] += 1
        return None
    if tipo == "attesa":
        # diretta o prima non ancora andata in onda: non e' un fallimento, ne'
        # un tentativo perso; si riprova a ogni giro finche' non esiste
        esito["tentati"] -= 1
        falliti[v["id"]] = {"title": v["title"], "reason": motivo,
                            "tentativi": falliti.get(v["id"], {}).get("tentativi", 0),
                            "date": _ora()}
        return None
    if tipo == "fallito" or not info:
        esito["falliti"] += 1
        tentativi = falliti.get(v["id"], {}).get("tentativi", 0) + 1
        if tentativi >= MAX_TENTATIVI:
            salta(v, motivo, "fallito")
        else:
            falliti[v["id"]] = {"title": v["title"], "reason": motivo,
                                "tentativi": tentativi, "date": _ora()}
            esito["saltati_info"].append({
                "slug": slug, "id": v["id"], "title": v["title"],
                "reason": motivo, "kind": "fallito"})
        return None
    return info


def _elenco_fallito(cfg, channel, testo):
    """Esito di uno show di cui non si e' ottenuto l'elenco dei video: nulla cambia."""
    _t, feed_url, count = _riga_da_stato(cfg, channel)
    return _esito(channel, feed_url, count, elenco_fallito=True,
                  elenco_errore=testo)


def process_channel(cfg, channel):
    slug = channel["slug"]
    log(f"\n>> {channel['title']}  [{slug}]")

    state_file = os.path.join(STATE_DIR, f"{slug}.json")
    state = _carica_stato(state_file)

    # canale manuale (--video): nessun feed da controllare, nessun download
    # automatico, nessuna cancellazione. Si limita a rigenerare l'XML.
    if channel.get("manual"):
        log(f"    canale manuale: {len(state['episodes'])} episodi, "
            f"aggiunti a mano con --video")
        feed_url, count = build_feed(cfg, channel, state["episodes"])
        return _esito(channel, feed_url, count)

    regole = regole_mod.regole_effettive(cfg, channel)
    known = {e["id"] for e in state["episodes"]}
    skipped = set(state.get("skipped", []))
    skipped_info = state["skipped_info"]
    falliti = state["falliti"]
    # gia' scaricati e poi cancellati perche' usciti dal feed: non vanno
    # riscaricati, altrimenti il ciclo si ripete all'infinito
    purged = set(state.get("purged", []))
    esito = _esito(channel, "", 0)

    max_ep = int(channel.get("max_episodes", cfg.get("max_episodes", 40)))
    try:
        _title, videos = fetch_channel_feed(channel["id"])
    except (urllib.error.URLError, ET.ParseError) as e:
        log(f"    feed RSS non disponibile ({e}); provo con yt-dlp")
        testo = str(e)
        try:
            videos = fetch_channel_via_ytdlp(channel["id"], max(30, max_ep * 3))
        except Errore as err:
            log(f"    {err}")
            videos, testo = [], str(err)
        if not videos:
            log("    nessun video ottenuto: salto questo canale")
            return _elenco_fallito(cfg, channel, testo)
        log(f"    ottenuti {len(videos)} video con yt-dlp")

    def salta(v, motivo, tipo):
        skipped.add(v["id"])
        skipped_info[v["id"]] = {"title": v["title"], "reason": motivo,
                                 "kind": tipo, "date": _ora()}
        falliti.pop(v["id"], None)
        esito["saltati_info"].append({"slug": slug, "id": v["id"],
                                      "title": v["title"], "reason": motivo,
                                      "kind": tipo})

    # inutile scaricare piu' video di quanti ne terra' il feed
    if all(v.get("published") for v in videos):
        videos.sort(key=lambda v: v["published"], reverse=True)
    nuovi = []
    for v in videos:
        if v["id"] in known or v["id"] in skipped or v["id"] in purged:
            continue
        entra, motivo = regole_mod.valuta(regole, v["title"], None)
        if not entra:
            log(f"    salto {v['id']} ({motivo})")
            salta(v, motivo, "regola")
            esito["saltati"] += 1
            continue
        nuovi.append(v)
    # scarica solo quanti ne mancano per arrivare a max_ep, partendo dai piu'
    # recenti: evita di scaricare 15 episodi per poi cancellarne 8
    if not cfg.get("keep_files", False):
        # tieni i max_ep piu' recenti fra quelli che hai e quelli disponibili:
        # senza questo, un archivio pieno di episodi vecchi blocca i nuovi
        insieme = [(e.get("published", ""), "v", e) for e in state["episodes"]]
        insieme += [(v.get("published", ""), "n", v) for v in nuovi]
        if all(t[0] for t in insieme):
            insieme.sort(key=lambda t: t[0], reverse=True)
            nuovi = [t[2] for t in insieme[:max_ep] if t[1] == "n"]
        else:
            # date inaffidabili: 'videos' e' gia' ordinata dal piu' recente,
            # quindi ogni video non ancora visto conta come piu' nuovo di
            # cio' che e' gia' in archivio (altrimenti, con l'archivio
            # pieno, i nuovi restano fuori per sempre)
            nuovi = nuovi[:max_ep]
    log(f"    {len(videos)} video nel feed YouTube, {len(nuovi)} da scaricare")

    audio_dir = os.path.join(AUDIO_ROOT, slug)
    for n, v in enumerate(nuovi, 1):
        _avanza_video(v, n, len(nuovi))
        info = _scarica_nuovo(slug, v, audio_dir, cfg, regole, esito,
                              falliti, salta)
        if info is None:
            continue
        filename = info["file"]
        full = os.path.join(audio_dir, filename)
        v["file"] = filename
        v["image"] = info.get("image")
        v["chapters"] = info.get("chapters", 0)
        v["tagliati_s"] = info.get("tagliati_s", 0)
        v["duration"] = get_duration(full)
        mb = human_mb(os.path.getsize(full))
        if mb > 95:
            log(f"    ATTENZIONE: {filename} pesa {mb:.0f} MB, oltre il limite "
                f"di 100 MB di Git. Abbassa max_duration_minutes o alza "
                f"audio_quality.")
        falliti.pop(v["id"], None)
        state["episodes"].append(v)
        esito["nuovi"] += 1

    # episodi gia' scaricati che ora sforano il limite di durata: via
    max_dur = regole["max_s"]
    if max_dur > 0:
        tenuti = []
        for ep in state["episodes"]:
            if ep.get("duration", 0) > max_dur:
                rimuovi_episodio(audio_dir, ep)
                log(f"    rimosso, troppo lungo ({hhmmss(ep.get('duration'))}): "
                    f"{ep['title'][:60]}")
                purged.add(ep["id"])
                esito["rimossi"] += 1
            else:
                tenuti.append(ep)
        state["episodes"] = tenuti

    state["episodes"].sort(key=lambda e: e.get("published", ""), reverse=True)

    if not cfg.get("keep_files", False) and len(state["episodes"]) > max_ep:
        for old in state["episodes"][max_ep:]:
            if old.get("file"):
                log(f"    rimosso vecchio episodio: {old['file']}")
            rimuovi_episodio(audio_dir, old)
            purged.add(old["id"])
            esito["rimossi"] += 1
        state["episodes"] = state["episodes"][:max_ep]

    state["skipped"] = sorted(skipped)
    state["purged"] = sorted(purged)
    save_json(state_file, state)

    feed_url, count = build_feed(cfg, channel, state["episodes"])
    esito["feed_url"], esito["count"] = feed_url, count
    return esito


CHIAVI_REGOLE = ("min_duration_minutes", "max_duration_minutes",
                 "title_include", "title_exclude", "sponsorblock")


def _regole_da_salvare(regole_extra):
    """Solo le regole note e con un valore: None, "" e liste vuote non si salvano."""
    return {k: regole_extra[k] for k in CHIAVI_REGOLE
            if k in (regole_extra or {}) and regole_extra[k] not in (None, "", [])}


def _slug_libero(cfg, titolo):
    slug = slugify(titolo)
    esistenti = {c["slug"] for c in cfg.get("channels", [])}
    base, n = slug, 2
    while slug in esistenti:
        slug, n = f"{base}-{n}", n + 1
    return slug


def add_channel(channel_id, titolo=None, regole_extra=None):
    """Aggiunge un canale a channels.json. Senza titolo lo ricava dal feed RSS.

    Restituisce lo slug (quello esistente, se il canale c'e' gia')."""
    channel_id = channel_id.strip()
    match = re.search(r"(UC[\w-]{22})", channel_id)
    if not match:
        raise Errore(
            "ERRORE: serve un channel ID che inizia per UC (24 caratteri).\n"
            "Per ricavarlo dall'indirizzo del canale:\n"
            '  yt-dlp --print "%(channel_id)s" --playlist-items 1 "URL_CANALE"')
    channel_id = match.group(1)

    cfg = load_json(CONFIG_FILE)
    if not cfg:
        raise Errore(f"ERRORE: manca {CONFIG_FILE}")

    for ch in cfg.get("channels", []):
        if ch.get("id") == channel_id:
            log(f"Il canale e' gia' presente come '{ch['slug']}'. Niente da fare.")
            return ch["slug"]

    videos = []
    if not titolo:
        log(f"Leggo il feed di {channel_id} ...")
        try:
            titolo, videos = fetch_channel_feed(channel_id)
        except (urllib.error.URLError, ET.ParseError) as e:
            raise Errore(f"ERRORE: il feed non risponde ({e}). Controlla il channel ID.")
    title = titolo or channel_id

    slug = _slug_libero(cfg, title)
    site = cfg["site_url"].rstrip("/")
    cfg.setdefault("channels", []).append({
        "id": channel_id,
        "slug": slug,
        "title": title,
        "description": f"Audio dei video del canale YouTube {title}.",
        "category": cfg.get("default_category", "Technology"),
        "cover": f"{site}/cover-{slug}.jpg",
        **_regole_da_salvare(regole_extra),
    })
    save_json(CONFIG_FILE, cfg)

    log(f"\nAggiunto: {title}")
    log(f"  slug:  {slug}")
    if videos:
        log(f"  video disponibili nel feed: {len(videos)}")
    log(f"  feed:  {site}/{slug}.xml")
    return slug


# -------------------------------------------------- video singoli (--video)

MANUAL_SLUG = "youtube"


def video_metadata(url):
    """Interroga yt-dlp e restituisce i dati del video (senza scaricarlo)."""
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-playlist", "--no-warnings", url],
            capture_output=True, text=True, timeout=TIMEOUT_YTDLP,
        )
    except subprocess.TimeoutExpired:
        raise Errore("ERRORE: yt-dlp non ha risposto in tempo (timed out).")
    if result.returncode != 0 or not result.stdout.strip():
        tail = (result.stderr or "").strip()[-400:]
        raise Errore(f"ERRORE: yt-dlp non riesce a leggere il video.\n{tail}")
    return json.loads(result.stdout.splitlines()[0])


def ensure_manual_channel(cfg):
    """Restituisce (creandolo se serve) il canale speciale per i video singoli."""
    for ch in cfg.get("channels", []):
        if ch.get("manual") or ch["slug"] == MANUAL_SLUG:
            ch["manual"] = True
            return ch

    site = cfg["site_url"].rstrip("/")
    channel = {
        "id": "",
        "slug": MANUAL_SLUG,
        "title": "YouTube",
        "description": "Video singoli aggiunti a mano da YouTube.",
        "category": cfg.get("default_category", "Technology"),
        "cover": f"{site}/cover-{MANUAL_SLUG}.jpg",
        "manual": True,
        # i video scelti a mano non vengono mai cancellati in automatico
        "max_episodes": 50,
    }
    cfg.setdefault("channels", []).append(channel)
    log(f"Creato il podcast \"{channel['title']}\" per i video singoli.")
    log(f"  feed: {site}/{MANUAL_SLUG}.xml")
    log(f"  copertina attesa in docs/cover-{MANUAL_SLUG}.jpg")
    return channel


def add_video(url, cfg):
    """Scarica un singolo video e lo aggiunge al podcast dei video sciolti.

    Restituisce lo slug del podcast dei video sciolti."""
    channel = ensure_manual_channel(cfg)
    slug = channel["slug"]
    state_file = os.path.join(STATE_DIR, f"{slug}.json")
    state = load_json(state_file, {"episodes": []})

    log("Leggo i dati del video ...")
    _avanza(testo="Leggo i dati del video…", show=slug)
    info = video_metadata(url)
    video_id = info.get("id")
    if not video_id:
        raise Errore("ERRORE: yt-dlp non ha restituito un id valido.")

    if any(e["id"] == video_id for e in state["episodes"]):
        log(f"Gia' presente in \"{channel['title']}\": {info.get('title', video_id)}")
        return slug

    titolo = (info.get("title") or video_id).strip()
    autore = (info.get("uploader") or info.get("channel") or "").strip()
    durata = int(info.get("duration") or 0)
    log(f"  {titolo}")
    log(f"  {autore} — {hhmmss(durata)}")

    _avanza(testo=f"Scarico: {titolo[:80]}", show=slug)
    if peso_sito_mb() >= LIMITE_SITO_MB:
        raise Errore(f"Non scarico il video, {MOTIVO_SPAZIO}: libera spazio "
                     f"togliendo qualche show o episodio.")
    audio_dir = os.path.join(AUDIO_ROOT, slug)
    # nessun filtro di durata qui: se lo chiedi tu, lo scarico
    esito_dl, motivo, tipo = download_audio(
        video_id, audio_dir,
        dict(cfg, min_duration_minutes=0, max_duration_minutes=0))
    if tipo or not esito_dl:
        raise Errore(f"Download fallito: {motivo or 'nessun file prodotto'}")
    filename = esito_dl["file"]

    full = os.path.join(audio_dir, filename)
    mb = human_mb(os.path.getsize(full))
    if mb > 95:
        log(f"ATTENZIONE: {filename} pesa {mb:.0f} MB, oltre il limite di 100 MB "
            f"di Git. Alza audio_quality e riprova.")

    descrizione = (info.get("description") or "").strip()
    if autore:
        descrizione = f"[{autore}]\n\n{descrizione}".strip()

    state["episodes"].append({
        "id": video_id,
        "title": titolo,
        # data di aggiunta, non di pubblicazione: cosi' finisce in cima al feed
        "published": datetime.now(timezone.utc).isoformat(),
        "description": descrizione,
        "file": filename,
        "image": esito_dl.get("image"),
        "chapters": esito_dl.get("chapters", 0),
        "tagliati_s": esito_dl.get("tagliati_s", 0),
        "duration": get_duration(full) or durata,
    })
    state["episodes"].sort(key=lambda e: e.get("published", ""), reverse=True)
    save_json(state_file, state)
    save_json(CONFIG_FILE, cfg)

    log(f"\nAggiunto a \"{channel['title']}\" ({len(state['episodes'])} episodi).")
    return slug


# ------------------------------------- aggiunta universale, anteprima, rimozione

MAX_EPISODI_PLAYLIST = 50
MB_AL_MINUTO = 0.5
MSG_PLAYLIST_PRIVATA = ('Questa playlist è privata o non esiste. '
                        'Rendila pubblica o "non in elenco" e riprova.')


def _classifica(link):
    c = regole_mod.classifica_link(link)
    if c["tipo"] == "sconosciuto":
        raise Errore("Questo non sembra un link di YouTube: incolla l'indirizzo "
                     "di un canale, di una playlist o di un video.")
    return c


def _avatar(d):
    """URL dell'immagine del canale fra le miniature restituite da yt-dlp."""
    miniature = [t for t in d.get("thumbnails") or [] if t.get("url")]
    for t in miniature:
        if str(t.get("id", "")).startswith("avatar"):
            return t["url"]
    return None


def info_sorgente(link):
    """Che cos'e' il link e come si chiama: {"tipo", "id", "title", "avatar_url", "url"}."""
    c = _classifica(link)
    if c["tipo"] == "video":
        meta = video_metadata(c["url"])
        return {"tipo": "video", "id": meta.get("id") or c["id"],
                "title": (meta.get("title") or c["id"]).strip(),
                "avatar_url": None, "url": c["url"]}
    if c["tipo"] == "playlist":
        try:
            d = ytdlp_json(["--flat-playlist", "--playlist-end", "1",
                            playlist_url(c["id"])])
        except Errore:
            raise Errore(MSG_PLAYLIST_PRIVATA)
        return {"tipo": "playlist", "id": c["id"],
                "title": (d.get("title") or c["id"]).strip(),
                "avatar_url": None, "url": c["url"]}
    base = (f"https://www.youtube.com/channel/{c['id']}" if c["id"] else c["url"])
    d = ytdlp_json(["--flat-playlist", "--playlist-end", "1", base])
    ident = c["id"] or d.get("channel_id")
    if not ident or not regole_mod.ID_CANALE.match(ident):
        raise Errore("ERRORE: non riesco a ricavare il canale da questo link.")
    titolo = d.get("channel") or d.get("uploader") or d.get("title") or ident
    return {"tipo": "canale", "id": ident, "title": titolo.strip(),
            "avatar_url": _avatar(d), "url": c["url"]}


def _episodi_video_singoli():
    return (load_json(os.path.join(STATE_DIR, f"{MANUAL_SLUG}.json"), {}) or {}
            ).get("episodes", [])


def _gia_presente(cfg, tipo, ident):
    if tipo == "video":
        return any(e["id"] == ident for e in _episodi_video_singoli())
    return any(ident in (c.get("id"), c.get("playlist_id"))
               for c in cfg.get("channels", []))


def anteprima(link, cfg, regole_extra=None):
    """Cosa succederebbe aggiungendo il link, senza scaricare ne' scrivere nulla."""
    c = _classifica(link)
    if c["tipo"] == "video":
        meta = video_metadata(c["url"])
        ident = meta.get("id") or c["id"]
        durata = int(meta.get("duration") or 0) or None
        titolo = (meta.get("title") or ident).strip()
        voci = [{"id": ident, "title": titolo, "duration": durata,
                 "entra": True, "motivo": ""}]
    else:
        info = info_sorgente(link)
        ident, titolo = info["id"], info["title"]
        if c["tipo"] == "playlist":
            elenco = elenco_ytdlp(playlist_url(ident), 0)[-MAX_EPISODI_PLAYLIST:]
            voci = [{"id": v["id"], "title": v["title"], "duration": v["duration"],
                     "entra": True, "motivo": ""} for v in elenco]
        else:
            regole = regole_mod.regole_effettive(
                cfg, _regole_da_salvare(regole_extra))
            elenco = elenco_ytdlp(
                f"https://www.youtube.com/channel/{ident}/videos", 10)[:10]
            voci = []
            for v in elenco:
                entra, motivo = regole_mod.valuta(regole, v["title"], v["duration"])
                voci.append({"id": v["id"], "title": v["title"],
                             "duration": v["duration"], "entra": entra,
                             "motivo": motivo})
    minuti = sum((v["duration"] or 0) for v in voci if v["entra"]) / 60
    return {"tipo": c["tipo"], "id": ident, "title": titolo,
            "gia_presente": _gia_presente(cfg, c["tipo"], ident),
            "video": voci, "stima_mb": int(minuti * MB_AL_MINUTO + 0.5)}


def scarica_file(url, dest):
    """Scarica un file (una copertina) su disco."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp, \
            open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def _copertina_da_file(slug, sorgente):
    """Copia un'immagine in docs/cover-SLUG.jpg ritagliata quadrata. True se riuscito."""
    os.makedirs(DOCS_DIR, exist_ok=True)
    tmp = os.path.join(DOCS_DIR, f"cover-{slug}.nuova.jpg")
    try:
        shutil.copyfile(sorgente, tmp)
        if not quadra_immagine(tmp, LATO_COPERTINA):
            return False
        os.replace(tmp, os.path.join(DOCS_DIR, f"cover-{slug}.jpg"))
        return True
    except OSError:
        return False
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _copertina_da_url(slug, url):
    """Copertina dello show da un indirizzo: se non riesce lo dice e va avanti."""
    _avanza(testo="Scarico la copertina…", show=slug)
    try:
        with tempfile.TemporaryDirectory() as d:
            grezza = os.path.join(d, "copertina.jpg")
            scarica_file(url, grezza)
            if _copertina_da_file(slug, grezza):
                log(f"  copertina: docs/cover-{slug}.jpg")
                return
    except Exception as e:
        log(f"  copertina non scaricata ({e})")
        return
    log("  copertina non scaricata (immagine non utilizzabile)")


def aggiungi(link, regole_extra=None):
    """Aggiunge un canale, una playlist o un video. Restituisce lo slug dello show."""
    cfg = load_json(CONFIG_FILE)
    if not cfg:
        raise Errore(f"ERRORE: manca {CONFIG_FILE}")
    c = _classifica(link)
    if c["tipo"] == "video":
        if _gia_presente(cfg, "video", c["id"]):
            raise Errore("Questo video è già nel podcast \"YouTube\".")
        return add_video(c["url"], cfg)

    _avanza(testo="Leggo la playlist…" if c["tipo"] == "playlist"
            else "Leggo il canale…")
    info = info_sorgente(link)
    if _gia_presente(cfg, info["tipo"], info["id"]):
        raise Errore(f"Già presente: {info['title']}.")
    extra = _regole_da_salvare(regole_extra)

    if info["tipo"] == "canale":
        slug = add_channel(info["id"], info["title"], extra)
        if info["avatar_url"]:
            _copertina_da_url(slug, info["avatar_url"])
        return slug

    cfg = load_json(CONFIG_FILE)
    slug = _slug_libero(cfg, info["title"])
    site = cfg["site_url"].rstrip("/")
    cfg.setdefault("channels", []).append({
        "id": info["id"],
        "playlist_id": info["id"],
        "type": "playlist",
        "slug": slug,
        "title": info["title"],
        "description": f"Audio dei video della playlist YouTube {info['title']}.",
        "category": cfg.get("default_category", "Technology"),
        "cover": f"{site}/cover-{slug}.jpg",
        "max_episodes": MAX_EPISODI_PLAYLIST,
        **extra,
    })
    save_json(CONFIG_FILE, cfg)
    log(f"\nAggiunta la playlist: {info['title']}  [{slug}]")
    primo = elenco_ytdlp(playlist_url(info["id"]), 1)
    if primo:
        _copertina_da_url(
            slug, f"https://i.ytimg.com/vi/{primo[0]['id']}/hqdefault.jpg")
    return slug


def rimuovi(slug):
    """Toglie uno show: blocco in channels.json, stato, feed, copertina e audio."""
    if not re.fullmatch(r"[a-z0-9-]+", slug or ""):
        raise Errore(f"Nome dello show non valido: {slug!r}")
    cfg = load_json(CONFIG_FILE)
    if not cfg:
        raise Errore(f"ERRORE: manca {CONFIG_FILE}")
    canali = cfg.get("channels", [])
    if not any(c.get("slug") == slug for c in canali):
        raise Errore(f"Show sconosciuto: {slug}")
    cfg["channels"] = [c for c in canali if c.get("slug") != slug]
    save_json(CONFIG_FILE, cfg)
    for p in (os.path.join(STATE_DIR, f"{slug}.json"),
              os.path.join(DOCS_DIR, f"{slug}.xml"),
              os.path.join(DOCS_DIR, f"cover-{slug}.jpg")):
        if os.path.exists(p):
            os.remove(p)
    shutil.rmtree(os.path.join(AUDIO_ROOT, slug), ignore_errors=True)
    log(f"Rimosso lo show {slug}.")


def cambia_copertina(slug, file_immagine):
    cfg = load_json(CONFIG_FILE) or {}
    if not any(c.get("slug") == slug for c in cfg.get("channels", [])):
        raise Errore(f"Show sconosciuto: {slug}")
    if not _copertina_da_file(slug, file_immagine):
        raise Errore("Non riesco a leggere questa immagine: scegline un'altra.")
    log(f"Copertina di {slug} aggiornata.")


def process_playlist(cfg, channel):
    """Lo show rispecchia la playlist: gli ultimi 50 video, ne' di piu' ne' di meno."""
    slug = channel["slug"]
    log(f"\n>> {channel['title']}  [{slug}]")
    state_file = os.path.join(STATE_DIR, f"{slug}.json")
    state = _carica_stato(state_file)

    try:
        voci = elenco_ytdlp(playlist_url(channel["playlist_id"]), 0)
    except Errore as err:
        # elenco non letto: non si tocca nulla
        log(f"    {err}")
        log("    salto questa playlist per questo giro")
        return _elenco_fallito(cfg, channel, str(err))
    if not voci:
        # lista vuota o non letta: meglio non toccare nulla
        log("    nessun video ottenuto: salto questa playlist")
        return _elenco_fallito(cfg, channel, "nessun video ottenuto")
    max_ep = int(channel.get("max_episodes", MAX_EPISODI_PLAYLIST))
    visti, ordine = set(), []
    for v in voci:
        if v["id"] not in visti:
            visti.add(v["id"])
            ordine.append(v)
    # un elenco molto piu' corto dell'ultimo buono e' quasi certamente tagliato:
    # fidarsene cancellerebbe i video nuovi e scaricherebbe i vecchi
    ultimo = state.get("ultimo_elenco", 0)
    if ultimo >= 4 and len(ordine) * 2 < ultimo:
        log(f"    elenco di {len(ordine)} video, l'ultimo ne aveva {ultimo}: "
            f"salto questa playlist")
        return _elenco_fallito(cfg, channel, "elenco della playlist incompleto")
    state["ultimo_elenco"] = len(ordine)
    target = ordine[-max_ep:]
    target_ids = {v["id"] for v in target}
    log(f"    {len(voci)} video nella playlist, ne tengo {len(target)}")

    audio_dir = os.path.join(AUDIO_ROOT, slug)
    esito = _esito(channel, "", 0)
    skipped = set(state.get("skipped", []))
    skipped_info = state["skipped_info"]
    falliti = state["falliti"]

    def salta(v, motivo, tipo):
        skipped.add(v["id"])
        skipped_info[v["id"]] = {"title": v["title"], "reason": motivo,
                                 "kind": tipo, "date": _ora()}
        falliti.pop(v["id"], None)
        esito["saltati_info"].append({"slug": slug, "id": v["id"],
                                      "title": v["title"], "reason": motivo,
                                      "kind": tipo})

    # fuori dalla playlist: via, prima di scaricare (libera spazio)
    tenuti = []
    for ep in state["episodes"]:
        if ep["id"] in target_ids:
            tenuti.append(ep)
        else:
            rimuovi_episodio(audio_dir, ep)
            log(f"    rimosso, non e' piu' in playlist: {ep['title'][:60]}")
            esito["rimossi"] += 1
    state["episodes"] = tenuti
    for ident in [i for i in falliti if i not in target_ids]:
        del falliti[ident]

    known = {e["id"] for e in state["episodes"]}
    nuovi = [v for v in target if v["id"] not in known and v["id"] not in skipped]
    # nessuna regola di durata o di titolo: la playlist l'hai scelta tu
    regole = dict(regole_mod.regole_effettive(cfg, channel),
                  min_s=0, max_s=0, solo=[], salta=[])
    # "visto la prima volta" = adesso; nell'ordine della playlist, un secondo
    # l'uno dall'altro, cosi' l'ultimo della playlist e' il piu' recente
    adesso = datetime.now(timezone.utc)
    for n, v in enumerate(nuovi):
        _avanza_video(v, n + 1, len(nuovi))
        info = _scarica_nuovo(slug, v, audio_dir, cfg, regole, esito,
                              falliti, salta)
        if info is None:
            continue
        full = os.path.join(audio_dir, info["file"])
        mb = human_mb(os.path.getsize(full))
        if mb > 95:
            log(f"    ATTENZIONE: {info['file']} pesa {mb:.0f} MB, oltre il "
                f"limite di 100 MB di Git.")
        falliti.pop(v["id"], None)
        state["episodes"].append({
            "id": v["id"], "title": v["title"],
            "published": (adesso - timedelta(
                seconds=len(nuovi) - 1 - n)).isoformat(),
            "description": v.get("description", ""),
            "file": info["file"], "image": info.get("image"),
            "chapters": info.get("chapters", 0),
            "tagliati_s": info.get("tagliati_s", 0),
            "duration": get_duration(full)})
        esito["nuovi"] += 1

    state["episodes"].sort(key=lambda e: e.get("published", ""), reverse=True)
    state["skipped"] = sorted(skipped)
    save_json(state_file, state)

    feed_url, count = build_feed(cfg, channel, state["episodes"])
    esito["feed_url"], esito["count"] = feed_url, count
    return esito


# ---------------------------------------------------------------- il giro

def _riga_da_stato(cfg, channel):
    """Titolo, indirizzo del feed e numero di episodi di uno show non elaborato."""
    stato = load_json(os.path.join(STATE_DIR, f"{channel['slug']}.json"), {}) or {}
    max_ep = int(channel.get("max_episodes", cfg.get("max_episodes", 40)))
    return (channel["title"],
            f"{cfg['site_url'].rstrip('/')}/{channel['slug']}.xml",
            min(len(stato.get("episodes", [])), max_ep))


def _show_da_stato(cfg, channel):
    _titolo, feed_url, count = _riga_da_stato(cfg, channel)
    return _esito(channel, feed_url, count)


def _errore_da_testo(testo):
    if testo.startswith("spazio esaurito"):
        return {"codice": "spazio",
                "messaggio": "Il sito pubblicato è pieno",
                "rimedio": "Togli qualche show o abbassa il numero di episodi."}
    return stato_giro.traduci_errore(testo)


PRIORITA_ERRORI = ["rete", "git", "spazio", "disco", "youtube", "comando", "sconosciuto"]


def _errori_del_giro(show, eccezioni, extra):
    """Errori leggibili, uno per codice: dai motivi dei fallimenti e dalle eccezioni."""
    testi = [e["reason"] for s in show for e in s["saltati_info"]
             if e["kind"] == "fallito"]
    testi += [s["elenco_errore"] for s in show if s.get("elenco_fallito")]
    testi += eccezioni
    errori, visti = [], set()
    for e in list(extra) + [_errore_da_testo(t) for t in testi]:
        if e["codice"] not in visti:
            visti.add(e["codice"])
            errori.append(e)
    # il primo e' quello che il pannello mette in evidenza: prima la causa
    # piu' a monte (senza rete il resto e' solo una conseguenza)
    errori.sort(key=lambda e: (PRIORITA_ERRORI.index(e["codice"])
                               if e["codice"] in PRIORITA_ERRORI
                               else len(PRIORITA_ERRORI)))
    return errori


def giro(cfg, solo=None, azione="aggiorna", elabora=True, errori_extra=None):
    """Un giro completo: elabora gli show, ricostruisce l'indice, pubblica.

    solo=SLUG elabora solo quello show (gli altri sono elencati dallo stato);
    elabora=False non scarica nulla e ricostruisce solo indice e pubblicazione.
    Scrive sempre il riepilogo (chiudi_giro) e lo restituisce."""
    global _avanzamento
    canali = cfg.get("channels", [])
    for channel in canali:
        if not channel.get("slug"):
            channel["slug"] = slugify(channel.get("title", channel.get("id", "")))
    if solo and not any(c["slug"] == solo for c in canali):
        raise Errore(f"Show sconosciuto: {solo}")

    inizio = (_avanzamento or {}).get("inizio") or _ora()
    show, eccezioni, elaborati, completato = [], [], [], False
    _avanzamento = {"azione": azione, "inizio": inizio, "show_tot": len(canali),
                    "video_n": 0, "video_tot": 0, "testo": "Inizio"}
    try:
        os.makedirs(DOCS_DIR, exist_ok=True)
        for n, channel in enumerate(canali, 1):
            if not elabora or (solo and channel["slug"] != solo):
                show.append(_show_da_stato(cfg, channel))
                continue
            _avanza(show=channel["slug"], titolo=channel["title"], show_n=n,
                    video_n=0, video_tot=0, testo=f"Controllo {channel['title']}")
            procedi = (process_playlist if channel.get("type") == "playlist"
                       else process_channel)
            try:
                risultato = procedi(cfg, channel)
            except Exception as e:
                log(f"\nErrore nello show {channel['slug']}: {e}")
                eccezioni.append(str(e) or e.__class__.__name__)
                risultato = dict(_show_da_stato(cfg, channel), errore_show=True)
            elaborati.append((channel, risultato))
            show.append(risultato)

        _avanza(show=None, titolo=None, video_n=0, video_tot=0,
                testo="Preparo la pubblicazione")
        build_index(cfg, show)
        completato = True
        if cfg.get("git_auto_push", True):
            try:
                git_publish(cfg)
            except Errore as e:
                completato = False
                eccezioni.append(str(e))
    except Exception as e:
        completato = False
        eccezioni.append(str(e) or e.__class__.__name__)
        log(f"\nGiro interrotto: {e}")
    finally:
        _avanzamento = None
        tentati = sum(s["tentati"] for s in show)
        falliti = sum(s["falliti"] for s in show)
        errori = _errori_del_giro(show, eccezioni, errori_extra or [])
        esito = stato_giro.esito_giro(completato, tentati, falliti)
        risultati = [r for _c, r in elaborati]
        remoti = [r for c, r in elaborati if not c.get("manual")]
        if remoti and all(r["elenco_fallito"] for r in remoti):
            esito = "fallito"
        elif risultati and all(r["errore_show"] for r in risultati):
            esito = "fallito"
        elif esito == "riuscito" and (
                errori_extra or any(s["elenco_fallito"] or s["errore_show"]
                                    for s in show)):
            esito = "problemi"
        riepilogo = {
            "azione": azione, "solo": solo, "inizio": inizio, "fine": _ora(),
            "completato": completato, "esito": esito, "show": show,
            "errori": errori,
            "saltati": [e for s in show for e in s["saltati_info"]],
        }
        # solo un giro completo conta per la sorveglianza delle 36 ore
        stato_giro.chiudi_giro(HERE, riepilogo,
                               aggiorna_riuscito=solo is None and elabora)

    if esito == "fallito":
        stato_giro.notifica(
            "Podcast", "L'aggiornamento non è riuscito. Apri il pannello per i dettagli.")
    log("\nFatto.")
    for s in show:
        log(f"  {s['title']}: {s['feed_url']}")
    return riepilogo


# ---------------------------------------------------------------- main

CODICE_BLOCCO_OCCUPATO = 3


def main():
    codice = _main()
    if codice:
        sys.exit(codice)


def _argomenti(opzione, quanti, uso):
    """Valori che seguono l'opzione nella riga di comando, o uscita con l'uso."""
    i = sys.argv.index(opzione)
    valori = sys.argv[i + 1:i + 1 + quanti]
    if len(valori) < quanti:
        log(f"USO: python3 yt2podcast.py {opzione} {uso}")
        sys.exit(1)
    return valori


def _carica_cfg():
    cfg = load_json(CONFIG_FILE)
    if not cfg:
        raise Errore(f"ERRORE: manca {CONFIG_FILE}")
    return cfg


def _regole_da_riga():
    """Regole passate con --regole JSON, o None."""
    if "--regole" not in sys.argv:
        return None
    testo, = _argomenti("--regole", 1, "JSON")
    try:
        regole = json.loads(testo)
    except json.JSONDecodeError:
        raise Errore("ERRORE: --regole non e' un JSON valido.")
    if not isinstance(regole, dict):
        raise Errore("ERRORE: --regole deve essere un oggetto JSON.")
    return regole


def _anteprima_cli(link, regole_extra=None):
    """--preview: su stdout solo un oggetto JSON, anche in caso di errore."""
    try:
        regole_extra = regole_extra if regole_extra is not None else _regole_da_riga()
        check_requirements()
        risultato = anteprima(link, _carica_cfg(), regole_extra)
    except Exception as e:
        print(json.dumps({"errore": str(e) or e.__class__.__name__},
                         ensure_ascii=False), flush=True)
        sys.exit(1)
    print(json.dumps(risultato, ensure_ascii=False), flush=True)


def _aggiorna_programma():
    """uv tool upgrade yt-dlp. Restituisce la lista degli errori (vuota se riuscito)."""
    log("Aggiorno il programma che scarica i video ...")
    _avanza(testo="Aggiorno il programma che scarica i video…")
    try:
        r = subprocess.run(["uv", "tool", "upgrade", "yt-dlp"],
                           capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            return []
        log((r.stderr or r.stdout or "").strip())
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"    {e}")
    return [{"codice": "aggiornamento",
             "messaggio": "Non sono riuscito ad aggiornare il programma che scarica i video",
             "rimedio": "Riprova più tardi, oppure lancia 'uv tool upgrade yt-dlp' da Terminale."}]


def _azione_da_riga():
    for opzione, azione in (("--add", "aggiungi"), ("--video", "video"),
                            ("--remove", "rimuovi"), ("--cover", "copertina"),
                            ("--aggiorna-programma", "aggiorna-programma")):
        if opzione in sys.argv:
            return azione
    return "aggiorna"


def _chiudi_comando_fallito(azione, inizio, eccezione):
    """Un comando finito male fuori da giro: il pannello lo legge da
    ultimo-comando.json, l'ultimo giro resta quello che era."""
    if isinstance(eccezione, Errore):
        messaggio = str(eccezione)
    else:
        messaggio = f"Errore imprevisto: {eccezione}"
    stato_giro.scrivi_ultimo_comando(HERE, azione, inizio, _ora(), messaggio)


def _sotto_blocco():
    """Il lavoro vero, con il blocco gia' preso. Restituisce il codice di uscita."""
    global _avanzamento
    azione, inizio = _azione_da_riga(), _ora()
    _avanzamento = {"azione": azione, "inizio": inizio, "video_n": 0,
                    "video_tot": 0, "testo": "Sto lavorando…"}
    try:
        return _esegui(azione)
    except Exception as e:
        _chiudi_comando_fallito(azione, inizio, e)
        raise
    finally:
        # qualunque sia l'uscita, nessun avanzamento resta in giro
        _avanzamento = None
        try:
            os.remove(os.path.join(HERE, "run", "avanzamento.json"))
        except FileNotFoundError:
            pass


def _esegui(azione):
    if "--add" in sys.argv:
        link, = _argomenti("--add", 1, "LINK")
        slug = aggiungi(link, _regole_da_riga())
        return _codice(giro(_carica_cfg(), solo=slug, azione=azione))

    if "--video" in sys.argv:
        url, = _argomenti("--video", 1, '"URL_DEL_VIDEO"')
        slug = add_video(url, _carica_cfg())
        return _codice(giro(_carica_cfg(), solo=slug, azione=azione))

    if "--remove" in sys.argv:
        slug, = _argomenti("--remove", 1, "SLUG")
        titolo = next((c.get("title", slug)
                       for c in _carica_cfg().get("channels", [])
                       if c.get("slug") == slug), slug)
        _avanza(testo=f"Rimuovo {titolo}…", show=slug)
        rimuovi(slug)
        return _codice(giro(_carica_cfg(), elabora=False, azione=azione))

    if "--cover" in sys.argv:
        slug, file_immagine = _argomenti("--cover", 2, "SLUG FILE")
        _avanza(testo="Preparo la copertina…", show=slug)
        cambia_copertina(slug, file_immagine)
        return _codice(giro(_carica_cfg(), elabora=False, azione=azione))

    cfg = _carica_cfg()
    if "TUOUTENTE" in cfg.get("site_url", ""):
        raise Errore("ERRORE: devi ancora impostare 'site_url' in channels.json")
    solo = None
    if "--only" in sys.argv:
        solo, = _argomenti("--only", 1, "SLUG")
    if "--aggiorna-programma" in sys.argv:
        return _codice(giro(cfg, solo=solo, azione=azione,
                            errori_extra=_aggiorna_programma()))
    return _codice(giro(cfg, solo=solo, azione=azione))


def _codice(riepilogo):
    return 2 if riepilogo["esito"] == "fallito" else 0


def _main():
    """Esegue la riga di comando. Restituisce il codice di uscita."""
    try:
        if "--preview" in sys.argv:
            _anteprima_cli(*_argomenti("--preview", 1, "LINK"))
            return 0
        try:
            check_requirements()
        except Errore as e:
            # prima del blocco, ma il pannello deve poterlo mostrare
            adesso = _ora()
            stato_giro.scrivi_ultimo_comando(
                HERE, _azione_da_riga(), adesso, adesso, str(e))
            raise
        if "--regole" in sys.argv:
            _regole_da_riga()   # JSON non valido: errore prima di prendere il blocco
        attesa = 0 if "--senza-attesa" in sys.argv else 1800
        try:
            with stato_giro.Blocco(HERE, attesa_s=attesa):
                return _sotto_blocco()
        except stato_giro.Occupato:
            log("C'è già un aggiornamento in corso: riprova tra poco.")
            stato_giro.notifica(
                "Podcast", "C'è già un aggiornamento in corso: riprova tra poco.")
            return CODICE_BLOCCO_OCCUPATO
    except Errore as e:
        log(str(e))
        return 1


if __name__ == "__main__":
    main()
