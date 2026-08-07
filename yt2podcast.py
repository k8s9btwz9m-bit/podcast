#!/usr/bin/env python3
"""
yt2podcast.py — da canale YouTube a feed podcast.  (versione 4)

USO NORMALE (scarica novita', rigenera i feed, pubblica su GitHub):
    python3 yt2podcast.py

AGGIUNGERE UN CANALE (basta il channel ID, il resto lo ricava da solo):
    python3 yt2podcast.py --add UCwNHueUsA11t3IZkgXLZ1xg

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
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

HERE = os.path.dirname(os.path.abspath(__file__))
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
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


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
        log("ERRORE: programmi mancanti: " + ", ".join(missing))
        sys.exit(1)


def git(*args, check=True):
    """Esegue un comando git nella cartella del progetto."""
    result = subprocess.run(["git", *args], cwd=HERE,
                            capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} fallito:\n{result.stderr.strip()}")
    return result.stdout.strip()


# ---------------------------------------------------------------- YouTube

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


def build_match_filter(cfg):
    """Filtro yt-dlp: niente dirette, niente clip brevi, niente video lunghissimi."""
    parts = ["!is_live"]
    min_s = int(cfg.get("min_duration_minutes", 2)) * 60
    max_s = int(cfg.get("max_duration_minutes", 120)) * 60
    if min_s > 0:
        parts.append(f"duration > {min_s}")
    if max_s > 0:
        parts.append(f"duration < {max_s}")
    return " & ".join(parts)


def download_audio(video_id, dest_dir, cfg):
    """Scarica l'audio come MP3. Restituisce il nome del file, o None."""
    os.makedirs(dest_dir, exist_ok=True)
    cmd = [
        "yt-dlp",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", str(cfg.get("audio_quality", "7")),
        "--embed-thumbnail",
        "--embed-metadata",
        "--no-playlist",
        "--retries", "3",
        "--no-progress",
        "--match-filter", build_match_filter(cfg),
    ]
    if cfg.get("mono", False):
        # una voce non guadagna nulla dallo stereo: dimezza il peso
        cmd += ["--postprocessor-args", "ffmpeg:-ac 1"]
    cmd += [
        "-o", os.path.join(dest_dir, "%(id)s.%(ext)s"),
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    log(f"    scarico {video_id} ...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    path = os.path.join(dest_dir, f"{video_id}.mp3")

    if not os.path.exists(path):
        tail = (result.stderr or result.stdout or "").strip()[-500:]
        motivo = tail.splitlines()[-1] if tail else "nessun file prodotto"
        log(f"    saltato {video_id} ({motivo})")
        # ripulisce eventuali residui (miniature, file parziali)
        for leftover in os.listdir(dest_dir):
            if leftover.startswith(video_id) and not leftover.endswith(".mp3"):
                os.remove(os.path.join(dest_dir, leftover))
        return None
    return f"{video_id}.mp3"


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


def build_feed(cfg, channel, episodes):
    site = cfg["site_url"].rstrip("/")
    slug = channel["slug"]
    feed_url = f"{site}/{slug}.xml"
    cover_url = channel.get("cover") or f"{site}/cover.jpg"
    author = channel.get("author") or cfg.get("author", "")
    max_ep = int(channel.get("max_episodes", cfg.get("max_episodes", 40)))
    desc_ch = channel.get("description", channel["title"])

    items = []
    for ep in episodes[:max_ep]:
        path = os.path.join(AUDIO_ROOT, slug, ep["file"])
        if not os.path.exists(path):
            continue
        size = os.path.getsize(path)
        audio_url = f"{site}/audio/{slug}/{ep['file']}"
        desc = ep.get("description", "")[:3000]
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
    <link>https://www.youtube.com/channel/{channel['id']}</link>
    <language>{escape(channel.get('language', cfg.get('language', 'it')))}</language>
    <lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>
    <atom:link href={quoteattr(feed_url)} rel="self" type="application/rss+xml"/>
    <image>
      <url>{escape(cover_url)}</url>
      <title>{escape(channel['title'])}</title>
      <link>https://www.youtube.com/channel/{channel['id']}</link>
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


def build_index(cfg, rows):
    site = cfg["site_url"].rstrip("/")
    lis = "\n".join(
        f'    <li><strong>{escape(t)}</strong> — {n} episodi<br>'
        f'<code>{escape(u)}</code></li>'
        for t, u, n in rows
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
    # se l'identita' era impostata solo nel repository, si e' persa con .git
    for key, value in identity.items():
        if value:
            git("config", key, value)
    git("add", "-A")
    git("commit", "-m",
        f"reset cronologia {datetime.now().strftime('%Y-%m-%d')}")
    git("remote", "add", "origin", remote_url)
    git("push", "-f", "-u", "origin", branch)
    log(f"    cronologia azzerata: ora {human_mb(git_dir_size()):.0f} MB")


def git_publish(cfg):
    """Commit + push. Se il repository e' troppo grande, azzera la cronologia."""
    if not os.path.isdir(GIT_DIR):
        log("\nNessun repository git qui: salto la pubblicazione.")
        return

    try:
        remote_url = git("remote", "get-url", "origin")
    except RuntimeError:
        log("\nNessun remote 'origin' configurato: salto la pubblicazione.")
        return

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

    try:
        git("push", "-u", "origin", branch)
        log("    push completato")
    except RuntimeError as e:
        log(f"    PUSH FALLITO:\n{e}")
        log("    I file in locale sono a posto. Se il rifiuto e' 'fetch first',")
        log("    guarda cosa manca con:  git log --oneline HEAD..origin/main")


# ---------------------------------------------------------------- canali

def process_channel(cfg, channel):
    slug = channel["slug"]
    log(f"\n>> {channel['title']}  [{slug}]")

    state_file = os.path.join(STATE_DIR, f"{slug}.json")

    # canale manuale (--video): nessun feed da controllare, nessun download
    # automatico, nessuna cancellazione. Si limita a rigenerare l'XML.
    if channel.get("manual"):
        state = load_json(state_file, {"episodes": []})
        log(f"    canale manuale: {len(state['episodes'])} episodi, "
            f"aggiunti a mano con --video")
        feed_url, count = build_feed(cfg, channel, state["episodes"])
        return channel["title"], feed_url, count

    state = load_json(state_file, {"episodes": []})
    known = {e["id"] for e in state["episodes"]}
    skipped = set(state.get("skipped", []))
    # gia' scaricati e poi cancellati perche' usciti dal feed: non vanno
    # riscaricati, altrimenti il ciclo si ripete all'infinito
    purged = set(state.get("purged", []))

    try:
        _title, videos = fetch_channel_feed(channel["id"])
    except (urllib.error.URLError, ET.ParseError) as e:
        log(f"    ERRORE nel leggere il feed del canale: {e}")
        return None

    # inutile scaricare piu' video di quanti ne terra' il feed
    videos.sort(key=lambda v: v.get("published", ""), reverse=True)
    max_ep = int(channel.get("max_episodes", cfg.get("max_episodes", 40)))
    candidati = videos if cfg.get("keep_files", False) else videos[:max_ep]

    nuovi = [v for v in candidati
             if v["id"] not in known and v["id"] not in skipped
             and v["id"] not in purged]
    log(f"    {len(videos)} video nel feed YouTube, {len(nuovi)} da scaricare")

    audio_dir = os.path.join(AUDIO_ROOT, slug)
    for v in nuovi:
        filename = download_audio(v["id"], audio_dir, cfg)
        if not filename:
            skipped.add(v["id"])
            continue
        full = os.path.join(audio_dir, filename)
        v["file"] = filename
        v["duration"] = get_duration(full)
        mb = human_mb(os.path.getsize(full))
        if mb > 95:
            log(f"    ATTENZIONE: {filename} pesa {mb:.0f} MB, oltre il limite "
                f"di 100 MB di Git. Abbassa max_duration_minutes o alza "
                f"audio_quality.")
        state["episodes"].append(v)

    # episodi gia' scaricati che ora sforano il limite di durata: via
    max_dur = int(cfg.get("max_duration_minutes", 120)) * 60
    if max_dur > 0:
        tenuti = []
        for ep in state["episodes"]:
            if ep.get("duration", 0) > max_dur:
                p = os.path.join(audio_dir, ep.get("file", ""))
                if ep.get("file") and os.path.exists(p):
                    os.remove(p)
                log(f"    rimosso, troppo lungo ({hhmmss(ep.get('duration'))}): "
                    f"{ep['title'][:60]}")
                purged.add(ep["id"])
            else:
                tenuti.append(ep)
        state["episodes"] = tenuti

    state["episodes"].sort(key=lambda e: e.get("published", ""), reverse=True)

    if not cfg.get("keep_files", False) and len(state["episodes"]) > max_ep:
        for old in state["episodes"][max_ep:]:
            p = os.path.join(audio_dir, old.get("file", ""))
            if old.get("file") and os.path.exists(p):
                os.remove(p)
                log(f"    rimosso vecchio episodio: {old['file']}")
            purged.add(old["id"])
        state["episodes"] = state["episodes"][:max_ep]

    state["skipped"] = sorted(skipped)
    state["purged"] = sorted(purged)
    save_json(state_file, state)

    feed_url, count = build_feed(cfg, channel, state["episodes"])
    return channel["title"], feed_url, count


def add_channel(channel_id):
    """Aggiunge un canale a channels.json ricavando il nome dal feed."""
    channel_id = channel_id.strip()
    match = re.search(r"(UC[\w-]{22})", channel_id)
    if not match:
        log("ERRORE: serve un channel ID che inizia per UC (24 caratteri).")
        log("Per ricavarlo dall'indirizzo del canale:")
        log('  yt-dlp --print "%(channel_id)s" --playlist-items 1 "URL_CANALE"')
        sys.exit(1)
    channel_id = match.group(1)

    cfg = load_json(CONFIG_FILE)
    if not cfg:
        log(f"ERRORE: manca {CONFIG_FILE}")
        sys.exit(1)

    for ch in cfg.get("channels", []):
        if ch["id"] == channel_id:
            log(f"Il canale e' gia' presente come '{ch['slug']}'. Niente da fare.")
            return

    log(f"Leggo il feed di {channel_id} ...")
    try:
        title, videos = fetch_channel_feed(channel_id)
    except (urllib.error.URLError, ET.ParseError) as e:
        log(f"ERRORE: il feed non risponde ({e}). Controlla il channel ID.")
        sys.exit(1)

    if not title:
        title = channel_id

    slug = slugify(title)
    esistenti = {c["slug"] for c in cfg.get("channels", [])}
    base, n = slug, 2
    while slug in esistenti:
        slug, n = f"{base}-{n}", n + 1

    site = cfg["site_url"].rstrip("/")
    cfg.setdefault("channels", []).append({
        "id": channel_id,
        "slug": slug,
        "title": title,
        "description": f"Audio dei video del canale YouTube {title}.",
        "category": cfg.get("default_category", "Technology"),
        "cover": f"{site}/cover-{slug}.jpg",
    })
    save_json(CONFIG_FILE, cfg)

    log(f"\nAggiunto: {title}")
    log(f"  slug:  {slug}")
    log(f"  video disponibili nel feed: {len(videos)}")
    log(f"  feed:  {site}/{slug}.xml")
    log(f"\nCopertina attesa in docs/cover-{slug}.jpg (senza, il feed funziona")
    log("comunque ma l'app Podcast mostra un riquadro vuoto).")
    log("\nOra lancia: python3 yt2podcast.py")


# -------------------------------------------------- video singoli (--video)

MANUAL_SLUG = "youtube"


def video_metadata(url):
    """Interroga yt-dlp e restituisce i dati del video (senza scaricarlo)."""
    result = subprocess.run(
        ["yt-dlp", "--dump-json", "--no-playlist", "--no-warnings", url],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        tail = (result.stderr or "").strip()[-400:]
        log(f"ERRORE: yt-dlp non riesce a leggere il video.\n{tail}")
        sys.exit(1)
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
    """Scarica un singolo video e lo aggiunge al podcast dei video sciolti."""
    channel = ensure_manual_channel(cfg)
    slug = channel["slug"]
    state_file = os.path.join(STATE_DIR, f"{slug}.json")
    state = load_json(state_file, {"episodes": []})

    log("Leggo i dati del video ...")
    info = video_metadata(url)
    video_id = info.get("id")
    if not video_id:
        log("ERRORE: yt-dlp non ha restituito un id valido.")
        sys.exit(1)

    if any(e["id"] == video_id for e in state["episodes"]):
        log(f"Gia' presente in \"{channel['title']}\": {info.get('title', video_id)}")
        return

    titolo = (info.get("title") or video_id).strip()
    autore = (info.get("uploader") or info.get("channel") or "").strip()
    durata = int(info.get("duration") or 0)
    log(f"  {titolo}")
    log(f"  {autore} — {hhmmss(durata)}")

    audio_dir = os.path.join(AUDIO_ROOT, slug)
    # nessun filtro di durata qui: se lo chiedi tu, lo scarico
    filename = download_audio(video_id, audio_dir,
                              dict(cfg, min_duration_minutes=0,
                                   max_duration_minutes=0))
    if not filename:
        log("Download fallito: il video non e' stato aggiunto.")
        sys.exit(1)

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
        "duration": get_duration(full) or durata,
    })
    state["episodes"].sort(key=lambda e: e.get("published", ""), reverse=True)
    save_json(state_file, state)
    save_json(CONFIG_FILE, cfg)

    log(f"\nAggiunto a \"{channel['title']}\" ({len(state['episodes'])} episodi).")
    log("Ora lancia: python3 yt2podcast.py")


# ---------------------------------------------------------------- main

def main():
    check_requirements()

    if "--add" in sys.argv:
        i = sys.argv.index("--add")
        if i + 1 >= len(sys.argv):
            log("USO: python3 yt2podcast.py --add UCxxxxxxxxxxxxxxxxxxxxxx")
            sys.exit(1)
        add_channel(sys.argv[i + 1])
        return

    if "--video" in sys.argv:
        i = sys.argv.index("--video")
        if i + 1 >= len(sys.argv):
            log("USO: python3 yt2podcast.py --video \"URL_DEL_VIDEO\"")
            sys.exit(1)
        cfg = load_json(CONFIG_FILE)
        if not cfg:
            log(f"ERRORE: manca {CONFIG_FILE}")
            sys.exit(1)
        add_video(sys.argv[i + 1], cfg)
        return

    cfg = load_json(CONFIG_FILE)
    if not cfg:
        log(f"ERRORE: manca {CONFIG_FILE}")
        sys.exit(1)
    if "TUOUTENTE" in cfg.get("site_url", ""):
        log("ERRORE: devi ancora impostare 'site_url' in channels.json")
        sys.exit(1)

    os.makedirs(DOCS_DIR, exist_ok=True)
    rows = []
    for channel in cfg.get("channels", []):
        if not channel.get("slug"):
            channel["slug"] = slugify(channel.get("title", channel["id"]))
        result = process_channel(cfg, channel)
        if result:
            rows.append(result)

    build_index(cfg, rows)

    if cfg.get("git_auto_push", True):
        git_publish(cfg)

    log("\nFatto.")
    for title, url, _ in rows:
        log(f"  {title}: {url}")


if __name__ == "__main__":
    main()
