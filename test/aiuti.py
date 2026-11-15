"""Aiuti per i test: progetto finto, stato, feed e download senza rete."""
import contextlib
import json
import os
import shutil
from unittest import mock

import stato_giro
import yt2podcast

SITE = "https://k8s9btwz9m-bit.github.io/podcast"
ID_CANALE = "UCaaaaaaaaaaaaaaaaaaaaaa"

_pila = None


def installa(caso, durata=600):
    """Da chiamare in setUp: feed vuoto, get_duration fisso, tutto annullato a fine test."""
    global _pila
    _pila = contextlib.ExitStack()
    caso.addCleanup(_pila.close)
    _pila.enter_context(mock.patch.object(yt2podcast, "get_duration",
                                          return_value=durata))
    finto_feed([])
    finto_ytdlp_json(_risposta_predefinita)
    # le notifiche di sistema non partono mai dai test
    _pila.enter_context(mock.patch.object(stato_giro, "notifica"))


def progetto_finto(tmp):
    cfg = {
        "site_url": SITE, "author": "Test", "language": "en",
        "max_episodes": 7, "audio_quality": "9",
        "min_duration_minutes": 2, "max_duration_minutes": 120,
        "max_repo_mb": 700, "git_auto_push": False, "mono": True,
        "channels": [{
            "id": ID_CANALE, "slug": "a", "title": "A", "description": "d",
            "category": "Science", "cover": SITE + "/cover-a.jpg",
        }],
    }
    with open(os.path.join(tmp, "channels.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.makedirs(os.path.join(tmp, "state"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "docs", "audio"), exist_ok=True)
    yt2podcast.imposta_radice(tmp)
    return tmp


def leggi_cfg(radice):
    with open(os.path.join(radice, "channels.json"), encoding="utf-8") as f:
        return json.load(f)


def canale(radice, slug):
    return next(c for c in leggi_cfg(radice)["channels"] if c["slug"] == slug)


def scrivi_stato(radice, slug, d):
    with open(os.path.join(radice, "state", f"{slug}.json"), "w",
              encoding="utf-8") as f:
        json.dump(d, f)


def leggi_stato(radice, slug):
    with open(os.path.join(radice, "state", f"{slug}.json"),
              encoding="utf-8") as f:
        return json.load(f)


def finto_feed(video, titolo="A"):
    """Sostituisce fetch_channel_feed. `video`: id oppure (id, titolo); il primo e' il piu' recente."""
    elenco = []
    for i, v in enumerate(video):
        ident, tit = (v, f"Video {v}") if isinstance(v, str) else v
        elenco.append({"id": ident, "title": tit, "description": "",
                       "published": f"2026-01-{20 - i:02d}T10:00:00+00:00"})
    _pila.enter_context(mock.patch.object(
        yt2podcast, "fetch_channel_feed",
        side_effect=lambda _id: (titolo, [dict(v) for v in elenco])))
    return elenco


def finto_download(esiti):
    """Sostituisce download_audio. Gli id fuori da `esiti` riescono e creano ID.mp3 vuoto.
    Restituisce la lista degli id per cui e' stato tentato un download."""
    chiamati = []

    def falso(video_id, dest_dir, cfg, regole=None):
        chiamati.append(video_id)
        if video_id in esiti:
            return esiti[video_id]
        os.makedirs(dest_dir, exist_ok=True)
        open(os.path.join(dest_dir, f"{video_id}.mp3"), "wb").close()
        return {"file": f"{video_id}.mp3"}, "", ""

    _pila.enter_context(mock.patch.object(yt2podcast, "download_audio",
                                          side_effect=falso))
    return chiamati


def crea_file(radice, percorso_relativo, contenuto=b"x"):
    """Crea un file (con le cartelle) sotto `radice`; restituisce il percorso assoluto."""
    p = os.path.join(radice, *percorso_relativo.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(contenuto)
    return p


def finto_elenco(voci):
    """Sostituisce elenco_ytdlp. `voci`: lista di (id, titolo, durata); la prima e' la piu' recente
    (per le playlist, l'ordine e' quello della playlist)."""
    elenco = [{"id": i, "title": t, "duration": d,
               "published": f"2026-01-{20 - n:02d}T10:00:00+00:00"}
              for n, (i, t, d) in enumerate(voci)]
    _pila.enter_context(mock.patch.object(
        yt2podcast, "elenco_ytdlp",
        side_effect=lambda url, limite: [dict(v) for v in (
            elenco[:limite] if limite else elenco)]))
    return elenco


def finto_elenco_playlist(ids):
    return finto_elenco([(i, f"Video {i}", 600) for i in ids])


def finto_download_ok():
    """Ogni download riesce. Restituisce la lista degli id tentati."""
    return finto_download({})


def finto_ytdlp_json(risposta):
    """Sostituisce ytdlp_json. `risposta`: dict, eccezione o funzione(args)."""
    def falso(args):
        r = risposta(args) if callable(risposta) else risposta
        if isinstance(r, Exception):
            raise r
        return dict(r)
    _pila.enter_context(mock.patch.object(yt2podcast, "ytdlp_json",
                                          side_effect=falso))


def _risposta_predefinita(args):
    url = args[-1]
    if "list=" in url:
        return {"title": "Playlist finta"}
    return {"channel_id": url.split("/channel/")[1].split("/")[0]
            if "/channel/" in url else ID_CANALE,
            "channel": "Canale finto",
            "thumbnails": [{"id": "avatar_uncropped",
                            "url": "https://img.esempio/avatar.jpg"}]}


def _blocco_playlist(radice, slug):
    cfg = leggi_cfg(radice)
    blocco = {"id": f"PL_{slug}", "playlist_id": f"PL_{slug}", "type": "playlist",
              "slug": slug, "title": f"Playlist {slug}", "description": "d",
              "category": "Science", "cover": f"{SITE}/cover-{slug}.jpg",
              "max_episodes": 50}
    cfg["channels"].append(blocco)
    with open(os.path.join(radice, "channels.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return blocco


def aggiungi_playlist_finta(radice, slug, ids):
    """Aggiunge a channels.json un blocco playlist e imposta l'elenco finto."""
    blocco = _blocco_playlist(radice, slug)
    finto_elenco_playlist(ids)
    return blocco


def canale_playlist_nuovo(radice):
    """Blocco playlist "pl" appena aggiunto, senza toccare l'elenco finto."""
    return _blocco_playlist(radice, "pl")


@contextlib.contextmanager
def cattura_notifiche():
    """Raccoglie le notifiche inviate: lista di (titolo, testo)."""
    inviate = []
    with mock.patch.object(
            stato_giro, "notifica",
            side_effect=lambda titolo, testo: inviate.append((titolo, testo))):
        yield inviate


@contextlib.contextmanager
def finto_git(push_fallisce=False):
    """Il progetto sembra un repository git con un remote; i comandi git sono finti.

    Yield: la lista dei comandi git eseguiti (tuple di argomenti)."""
    eseguiti = []

    def falso(*args, check=True):
        eseguiti.append(args)
        if args[0] == "push" and push_fallisce:
            raise RuntimeError(
                f"git {' '.join(args)} fallito:\n! [rejected] main -> main")
        return {("remote", "get-url"): "https://github.com/x/y.git",
                ("symbolic-ref", "--short"): "main",
                ("status", "--porcelain"): " M docs/a.xml",
                }.get(args[:2], "")

    os.makedirs(yt2podcast.GIT_DIR, exist_ok=True)
    with mock.patch.object(yt2podcast, "git", side_effect=falso):
        try:
            yield eseguiti
        finally:
            shutil.rmtree(yt2podcast.GIT_DIR, ignore_errors=True)


@contextlib.contextmanager
def finto_peso_sito(mb):
    """Il sito pubblicato pesa `mb` megabyte."""
    with mock.patch.object(yt2podcast, "peso_sito_mb", return_value=mb):
        yield
