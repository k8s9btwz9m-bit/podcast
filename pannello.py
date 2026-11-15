#!/usr/bin/env python3
"""Pannello di controllo locale: un piccolo server su 127.0.0.1.

Legge channels.json, state/SLUG.json e i file di run/ direttamente, e lancia
yt2podcast.py solo come sottoprocesso. Solo libreria standard.
"""
import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import regole
from stato_giro import (Blocco, Occupato, in_corso, leggi_avanzamento,
                        notifica)

RADICE = os.path.dirname(os.path.abspath(__file__))
PORTA = 8719
LIMITE_SITO_MB = 900
MAX_JSON = 64 * 1024
MAX_IMMAGINE = 10 * 1024 * 1024
SLUG = re.compile(r"^[a-z0-9-]+$")
ID_VIDEO = re.compile(r"^[A-Za-z0-9_-]{11}$")
QUALITA = {"leggera": "9", "media": "7", "alta": "5"}
CHIAVI_REGOLE = ("min_duration_minutes", "max_duration_minutes",
                 "title_include", "title_exclude", "sponsorblock")
TIPI_IMMAGINE = {"image/jpeg": "jpg", "image/png": "png",
                 "image/webp": "webp", "image/heic": "heic"}
TIPI_STATICI = {"/": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/stile.css": ("stile.css", "text/css; charset=utf-8")}
GRAZIA_LANCIO_S = 3
GRAZIA_AVVIO = timedelta(minutes=30)
SOGLIA_FERMO = timedelta(hours=36)
INTERVALLO_AVVISI = timedelta(hours=24)
PASSO_SORVEGLIANZA_S = 300
SCARTO_RIPOSO = timedelta(minutes=2)
VALIDITA_ULTIMO_COMANDO = timedelta(minutes=10)


class Errore(Exception):
    def __init__(self, stato, messaggio):
        super().__init__(messaggio)
        self.stato = stato
        self.messaggio = messaggio


# ---------------------------------------------------------------- sicurezza

def ammessa(metodo, intestazioni, porta, percorso=""):
    """Host solo locale; per le richieste che non sono GET anche Origin,
    X-Podcast e tipo di contenuto (immagine per /api/copertina)."""
    h = {str(k).lower(): str(v) for k, v in intestazioni.items()}
    host_ok = {f"127.0.0.1:{porta}", f"localhost:{porta}"}
    if h.get("host") not in host_ok:
        return False
    if metodo == "GET":
        return True
    if h.get("origin") != "http://" + h["host"]:
        return False
    if h.get("x-podcast") != "1":
        return False
    tipo = h.get("content-type", "").split(";")[0].strip().lower()
    if percorso == "/api/copertina":
        return tipo.startswith("image/")
    return tipo == "application/json"


# --------------------------------------------------------------- sorveglianza

def da_notificare(adesso, avvio, ultimo_riuscito, ultima_notifica):
    if adesso - avvio < GRAZIA_AVVIO:
        return False
    if ultimo_riuscito is not None and adesso - ultimo_riuscito <= SOGLIA_FERMO:
        return False
    if ultima_notifica is not None and adesso - ultima_notifica < INTERVALLO_AVVISI:
        return False
    return True


def _data(testo):
    try:
        d = datetime.fromisoformat(testo)
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.astimezone()


def _leggi_json(percorso, predefinito=None):
    """Legge un JSON; durante un giro i file vengono riscritti, quindi un file
    mancante o a metà si riprova qualche volta prima di arrendersi."""
    for tentativo in range(3):
        try:
            with open(percorso, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            if tentativo < 2:
                time.sleep(0.005)
    return predefinito


def _scrivi_atomico(percorso, dati):
    """Scrittura atomica con un file temporaneo dal nome unico."""
    cartella = os.path.dirname(percorso)
    os.makedirs(cartella, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=cartella, prefix=os.path.basename(percorso) + ".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(dati)
        os.chmod(tmp, 0o644)
        os.replace(tmp, percorso)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _scrivi_json(percorso, dati):
    _scrivi_atomico(percorso, json.dumps(dati, ensure_ascii=False, indent=2).encode("utf-8"))


def _ultimo_riuscito(radice):
    d = _leggi_json(os.path.join(radice, "run", "ultimo-riuscito.json"), {})
    return _data(d.get("fine")) if isinstance(d, dict) else None


def controlla_sorveglianza(radice, avvio, adesso=None):
    """Un giro di controllo: avvisa se serve e ricorda l'ora dell'avviso."""
    adesso = adesso or datetime.now(timezone.utc)
    percorso = os.path.join(radice, "run", "sorveglianza.json")
    registro = _leggi_json(percorso, {})
    ultima = _data(registro.get("ultima_notifica")) if isinstance(registro, dict) else None
    if da_notificare(adesso, avvio, _ultimo_riuscito(radice), ultima):
        notifica("Podcast", "Nessun aggiornamento riuscito da più di 36 ore. "
                            "Apri il pannello.")
        _scrivi_json(percorso, {"ultima_notifica": adesso.isoformat()})
        return True
    return False


def ha_dormito(ora_prec, mono_prec, ora, mono):
    """True se fra due controlli il Mac e' stato in stop: l'orologio di parete
    e' avanzato molto piu' di quello monotono (che in stop si ferma)."""
    return (ora - ora_prec) - timedelta(seconds=mono - mono_prec) > SCARTO_RIPOSO


def passo_sorveglianza(radice, memoria, ora, mono):
    """Un controllo. `memoria`: {"avvio", "ora", "mono"} del controllo
    precedente. Dopo uno stop la grazia di 30 minuti riparte da adesso, cosi'
    il giro di recupero di launchd ha il tempo di finire."""
    if ha_dormito(memoria["ora"], memoria["mono"], ora, mono):
        memoria["avvio"] = ora
    memoria["ora"], memoria["mono"] = ora, mono
    return controlla_sorveglianza(radice, memoria["avvio"], ora)


def avvia_sorveglianza(radice):
    ora = datetime.now(timezone.utc)
    memoria = {"avvio": ora, "ora": ora, "mono": time.monotonic()}
    fermo = threading.Event()

    def ciclo():
        while True:
            try:
                passo_sorveglianza(radice, memoria, datetime.now(timezone.utc),
                                   time.monotonic())
            except Exception:
                traceback.print_exc()
            if fermo.wait(PASSO_SORVEGLIANZA_S):
                return

    threading.Thread(target=ciclo, daemon=True).start()
    return fermo


# ------------------------------------------------------------------- dati

def _cfg(radice):
    cfg = _leggi_json(os.path.join(radice, "channels.json"), None)
    if not isinstance(cfg, dict):
        raise Errore(503, "Riprova tra un attimo.")
    return cfg


def _canale(cfg, slug):
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise Errore(404, "Show sconosciuto.")
    for c in cfg.get("channels", []):
        if c.get("slug") == slug:
            return c
    raise Errore(404, "Show sconosciuto.")


def _percorso_stato(radice, slug):
    return os.path.join(radice, "state", slug + ".json")


def _stato_show(radice, slug):
    d = _leggi_json(_percorso_stato(radice, slug), {})
    return d if isinstance(d, dict) else {}


def _elenco_saltati(st):
    """Saltati e falliti di uno show: lo stesso elenco per conteggio e dettaglio."""
    info = st.get("skipped_info", {})
    saltati = []
    for ident in st.get("skipped", []):
        i = info.get(ident, {}) if isinstance(info, dict) else {}
        saltati.append({"id": ident, "title": i.get("title", ""),
                        "reason": i.get("reason", "motivo non registrato"),
                        "kind": i.get("kind", ""), "date": i.get("date", "")})
    for ident, f in (st.get("falliti") or {}).items():
        saltati.append({"id": ident, "title": f.get("title", ""),
                        "reason": f.get("reason", "motivo non registrato"),
                        "kind": "fallito", "date": f.get("date", "")})
    return saltati


def _mb_cartella(percorso):
    totale = 0
    for base, _, nomi in os.walk(percorso):
        for n in nomi:
            try:
                totale += os.lstat(os.path.join(base, n)).st_size
            except OSError:
                pass
    return totale / (1024 * 1024)


def _oggi(percorso):
    try:
        return datetime.fromtimestamp(os.path.getmtime(percorso)).date() \
            == datetime.now().date()
    except OSError:
        return False


def _tipo(canale):
    if canale.get("type") == "playlist":
        return "playlist"
    return "singoli" if canale.get("manual") else "canale"


def _nome_qualita(valore):
    try:
        n = int(valore)
    except (TypeError, ValueError):
        n = 7
    vicino = min((7, 9, 5), key=lambda q: abs(q - n))
    return {"9": "leggera", "7": "media", "5": "alta"}[str(vicino)]


def _feed_url(cfg, slug):
    return f"{cfg.get('site_url', '').rstrip('/')}/{slug}.xml"


def _prossimo_giro(adesso=None):
    adesso = adesso or datetime.now()
    for ora in (8, 20):
        if adesso.replace(hour=ora, minute=0, second=0, microsecond=0) > adesso:
            return f"{ora:02d}:00"
    return "08:00"


def _ultimo_comando(radice, adesso):
    """Il comando fallito fuori dal giro, finche' e' recente; altrimenti None."""
    d = _leggi_json(os.path.join(radice, "run", "ultimo-comando.json"))
    if not isinstance(d, dict):
        return None
    fine = _data(d.get("fine"))
    if fine is None or adesso - fine > VALIDITA_ULTIMO_COMANDO:
        return None
    return d


def costruisci_stato(radice):
    cfg = _cfg(radice)
    show = []
    for c in cfg.get("channels", []):
        slug = c.get("slug", "")
        st = _stato_show(radice, slug)
        cartella = os.path.join(radice, "docs", "audio", slug)
        nuovi = 0
        if os.path.isdir(cartella):
            nuovi = sum(1 for n in os.listdir(cartella)
                        if n.endswith(".mp3") and _oggi(os.path.join(cartella, n)))
        show.append({
            "slug": slug, "title": c.get("title", slug), "tipo": _tipo(c),
            "episodi": len(st.get("episodes", [])),
            "mb": round(_mb_cartella(cartella)), "nuovi_oggi": nuovi,
            "saltati": len(_elenco_saltati(st)),
            "feed_url": _feed_url(cfg, slug)})
    riuscito = _ultimo_riuscito(radice)
    adesso = datetime.now(timezone.utc)
    return {
        "in_corso": in_corso(radice),
        "avanzamento": leggi_avanzamento(radice),
        "ultimo_giro": _leggi_json(os.path.join(radice, "run", "ultimo-giro.json")),
        "ultimo_comando": _ultimo_comando(radice, adesso),
        "ultimo_riuscito": riuscito.isoformat() if riuscito else None,
        "fermo_da_36_ore": riuscito is None or adesso - riuscito > SOGLIA_FERMO,
        "prossimo_giro": _prossimo_giro(),
        "show": show,
        "impostazioni": {
            "max_episodes": cfg.get("max_episodes", 40),
            "min_duration_minutes": cfg.get("min_duration_minutes", 2),
            "max_duration_minutes": cfg.get("max_duration_minutes", 120),
            "qualita": _nome_qualita(cfg.get("audio_quality", "7")),
            "sponsorblock": bool(cfg.get("sponsorblock", True)),
            "loudnorm": bool(cfg.get("loudnorm", True)),
            "chapters": bool(cfg.get("chapters", True))},
        "spazio": {
            "cronologia_mb": round(_mb_cartella(os.path.join(radice, ".git"))),
            "soglia_mb": cfg.get("max_repo_mb", 800),
            "sito_mb": round(_mb_cartella(os.path.join(radice, "docs"))),
            "limite_sito_mb": LIMITE_SITO_MB}}


def costruisci_show(radice, slug):
    cfg = _cfg(radice)
    c = _canale(cfg, slug)
    st = _stato_show(radice, slug)
    feed = _feed_url(cfg, slug)
    audio = os.path.join(radice, "docs", "audio", slug)
    episodi = []
    for e in st.get("episodes", []):
        ident = e.get("id", "")
        episodi.append({
            "id": ident, "title": e.get("title", ""),
            "published": e.get("published", ""), "duration": e.get("duration"),
            "image": bool(ID_VIDEO.fullmatch(ident))
            and os.path.isfile(os.path.join(audio, ident + ".jpg")),
            "chapters": e.get("chapters", 0), "tagliati_s": e.get("tagliati_s", 0),
            "nuovo_oggi": bool(ID_VIDEO.fullmatch(ident))
            and _oggi(os.path.join(audio, ident + ".mp3"))})
    saltati = _elenco_saltati(st)
    return {"slug": slug, "title": c.get("title", slug), "feed_url": feed,
            "podcast_url": re.sub(r"^https://", "podcast://", feed),
            "episodi": episodi,
            "regole": {k: c[k] for k in CHIAVI_REGOLE if k in c},
            "saltati": saltati}


# ------------------------------------------------------------- validazione

def _intero(valore, minimo=0, massimo=None):
    ok = isinstance(valore, int) and not isinstance(valore, bool) and valore >= minimo \
        and (massimo is None or valore <= massimo)
    if not ok:
        raise Errore(400, "Valore numerico non valido.")
    return valore


def _booleano(valore):
    if not isinstance(valore, bool):
        raise Errore(400, "Valore sì/no non valido.")
    return valore


def _elenco_parole(valore):
    if isinstance(valore, str):
        grezzi = valore.split(",")
    elif isinstance(valore, list) and all(isinstance(v, str) for v in valore):
        grezzi = valore
    else:
        raise Errore(400, "Elenco di parole non valido.")
    return [p.strip() for p in grezzi if p.strip()]


def _link(corpo):
    """Link già ripulito da classifica_link; mai il testo grezzo."""
    link = corpo.get("link")
    if not isinstance(link, str) or not 1 <= len(link) <= 500:
        raise Errore(400, "Link non valido.")
    r = regole.classifica_link(link)
    if r["tipo"] == "sconosciuto" or r["url"].startswith("-"):
        raise Errore(400, "Non riconosco questo link. Incolla un link di YouTube: "
                          "canale, playlist o video.")
    return r["url"]


def _regole_valide(dati):
    """Valida un oggetto di regole (le sole cinque chiavi); restituisce
    {chiave: valore, oppure None se la chiave va tolta}."""
    sconosciute = set(dati) - set(CHIAVI_REGOLE)
    if sconosciute:
        raise Errore(400, "Regola sconosciuta.")
    modifiche = {}
    for chiave in CHIAVI_REGOLE:
        if chiave not in dati:
            continue
        v = dati[chiave]
        if v is None or v == "" or v == []:
            modifiche[chiave] = None
        elif chiave in ("min_duration_minutes", "max_duration_minutes"):
            modifiche[chiave] = _intero(v)
        elif chiave == "sponsorblock":
            modifiche[chiave] = _booleano(v)
        else:
            modifiche[chiave] = _elenco_parole(v) or None
    return modifiche


def _argomenti_regole(corpo):
    r = corpo.get("regole")
    if r is None:
        return []
    if not isinstance(r, dict):
        raise Errore(400, "Regole non valide.")
    pulite = {k: v for k, v in _regole_valide(r).items() if v is not None}
    return ["--regole", json.dumps(pulite)] if pulite else []


def _azzera_tentativi(st, ident):
    """Riporta un video fra quelli da riprovare, senza farlo sparire dal
    pannello: resta "fallito" con i tentativi a zero finche' non riesce.
    Modifica `st`; True se qualcosa e' cambiato."""
    cambiato = False
    if ident in st.get("skipped", []):
        st["skipped"] = [i for i in st["skipped"] if i != ident]
        info = st.get("skipped_info")
        voce = info.pop(ident, None) if isinstance(info, dict) else None
        cambiato = True
        if isinstance(voce, dict) and voce.get("kind") == "fallito":
            if not isinstance(st.get("falliti"), dict):
                st["falliti"] = {}
            st["falliti"][ident] = {
                "title": voce.get("title", ""),
                "reason": voce.get("reason", ""),
                "tentativi": 0, "date": voce.get("date", "")}
    f = st.get("falliti")
    if isinstance(f, dict) and isinstance(f.get(ident), dict) \
            and f[ident].get("tentativi") != 0:
        f[ident]["tentativi"] = 0
        cambiato = True
    return cambiato


def _id_video(corpo):
    ident = corpo.get("id")
    if not isinstance(ident, str) or not ID_VIDEO.fullmatch(ident):
        raise Errore(400, "Identificativo del video non valido.")
    return ident


# ---------------------------------------------------------------- server

def _lanciatore_predefinito(radice):
    def lancia(args):
        os.makedirs(os.path.join(radice, "run"), exist_ok=True)
        with open(os.path.join(radice, "run", "lavori.log"), "ab") as log:
            return subprocess.Popen(
                [sys.executable, os.path.join(radice, "yt2podcast.py"),
                 "--senza-attesa", *args],
                cwd=radice, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                start_new_session=True)
    return lancia


def _esecutore_predefinito(radice):
    def esegui(args, timeout):
        # processo in una sessione sua: allo scadere si ferma tutto il gruppo
        # (yt-dlp e ffmpeg compresi), non solo lo script
        p = subprocess.Popen(
            [sys.executable, os.path.join(radice, "yt2podcast.py"), *args],
            cwd=radice, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            uscita, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.communicate()
            raise
        return p.returncode, uscita
    return esegui


class Pannello(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, radice, porta, lanciatore, esecutore):
        super().__init__(("127.0.0.1", porta), Gestore)
        self.radice = radice
        self.lanciatore = lanciatore or _lanciatore_predefinito(radice)
        self.esecutore = esecutore or _esecutore_predefinito(radice)
        self.blocco = threading.RLock()
        self.ultimo_lancio = float("-inf")
        self.avviato = None  # ultimo processo lanciato, se il lanciatore lo restituisce

    def occupato(self):
        if in_corso(self.radice):
            return True
        p = self.avviato
        if p is not None and hasattr(p, "poll"):
            return p.poll() is None
        # lanciatore senza processo: il lavoro prende il blocco entro pochi secondi
        return time.monotonic() - self.ultimo_lancio < GRAZIA_LANCIO_S

    def esige_libero(self):
        if self.occupato():
            raise Errore(409, "C'è già un lavoro in corso.")

    def lancia(self, args):
        self.avviato = self.lanciatore(args)
        self.ultimo_lancio = time.monotonic()


@contextlib.contextmanager
def blocco_reale(radice):
    """Il blocco vero del giro (lo stesso di yt2podcast), senza attesa: serve
    a scrivere i file che anche un giro in partenza leggerebbe."""
    blocco = Blocco(radice, attesa_s=0)
    try:
        blocco.__enter__()
    except Occupato:
        raise Errore(409, "C'è già un lavoro in corso.")
    try:
        yield
    finally:
        blocco.__exit__(None, None, None)


def crea_server(radice, porta=PORTA, lanciatore=None, esecutore=None):
    return Pannello(os.path.abspath(radice), porta, lanciatore, esecutore)


class Gestore(BaseHTTPRequestHandler):
    server_version = "Pannello"
    sys_version = ""

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        frasi = {400: "Richiesta non valida.", 404: "Non trovato.",
                 405: "Metodo non consentito.", 414: "Richiesta non valida.",
                 431: "Richiesta non valida.", 501: "Metodo non supportato."}
        self.close_connection = True
        self._json(code, {"errore": frasi.get(code, "Richiesta non valida.")})

    # --- risposte
    def _invia(self, stato, corpo, tipo, api=False):
        self.send_response(stato)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        if api:
            self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(corpo)

    def _json(self, stato, dati):
        self._invia(stato, json.dumps(dati, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8", api=True)

    def _file(self, percorso, tipo):
        try:
            with open(percorso, "rb") as f:
                dati = f.read()
        except OSError:
            raise Errore(404, "Non trovato.")
        self._invia(200, dati, tipo)

    # --- ingresso
    def _gestisci(self):
        try:
            url = urlsplit(self.path)
            percorso = unquote(url.path)
            if not ammessa(self.command, self.headers, self.server.server_address[1],
                           percorso):
                self.close_connection = True
                raise Errore(403, "Richiesta non ammessa.")
            if self.command == "GET":
                self._get(percorso, parse_qs(url.query))
            elif self.command == "POST":
                self._post(percorso, parse_qs(url.query))
            else:
                raise Errore(405, "Metodo non consentito.")
        except Errore as e:
            self.close_connection = True
            self._json(e.stato, {"errore": e.messaggio})
        except Exception:
            traceback.print_exc()
            self.close_connection = True
            try:
                self._json(500, {"errore": "Errore interno del pannello."})
            except Exception:
                pass

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _gestisci

    # --- GET
    def _get(self, percorso, query):
        radice = self.server.radice
        if percorso in TIPI_STATICI:
            nome, tipo = TIPI_STATICI[percorso]
            return self._file(os.path.join(radice, "pannello", nome), tipo)
        if percorso == "/api/stato":
            return self._json(200, costruisci_stato(radice))
        m = re.fullmatch(r"/api/show/([^/]+)", percorso)
        if m:
            return self._json(200, costruisci_show(radice, m.group(1)))
        m = re.fullmatch(r"/copertine/([a-z0-9-]+)\.jpg", percorso)
        if m:
            return self._file(os.path.join(radice, "docs", f"cover-{m.group(1)}.jpg"),
                              "image/jpeg")
        m = re.fullmatch(r"/miniature/([a-z0-9-]+)/([A-Za-z0-9_-]{11})\.jpg", percorso)
        if m:
            return self._file(os.path.join(radice, "docs", "audio", m.group(1),
                                           m.group(2) + ".jpg"), "image/jpeg")
        m = re.fullmatch(r"/qr/([^/]+)\.svg", percorso)
        if m:
            return self._qr(m.group(1))
        if percorso.startswith("/api/") and percorso in POST_API:
            raise Errore(405, "Metodo non consentito.")
        raise Errore(404, "Non trovato.")

    def _qr(self, slug):
        radice = self.server.radice
        cfg = _cfg(radice)
        _canale(cfg, slug)
        cache = os.path.join(radice, "run", f"qr-{slug}.svg")
        if os.path.isfile(cache):
            return self._file(cache, "image/svg+xml")
        programma = os.path.join(radice, "run", "qr")
        if not os.access(programma, os.X_OK):
            raise Errore(503, "QR non disponibile.")
        url = costruisci_show(radice, slug)["podcast_url"]
        try:
            r = subprocess.run([programma, url], capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            raise Errore(503, "QR non disponibile.")
        if r.returncode != 0 or not r.stdout:
            raise Errore(503, "QR non disponibile.")
        _scrivi_atomico(cache, r.stdout)
        self._invia(200, r.stdout, "image/svg+xml")

    # --- POST
    def _leggi_corpo(self, massimo):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise Errore(400, "Richiesta non valida.")
        if n < 0:
            raise Errore(400, "Richiesta non valida.")
        if n > massimo:
            restante = min(n, 16 * 1024 * 1024)  # si svuota il canale per rispondere
            while restante > 0:
                pezzo = self.rfile.read(min(65536, restante))
                if not pezzo:
                    break
                restante -= len(pezzo)
            raise Errore(413, "Richiesta troppo grande.")
        return self.rfile.read(n)

    def _json_corpo(self):
        try:
            dati = json.loads(self._leggi_corpo(MAX_JSON).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise Errore(400, "Il corpo non è un JSON valido.")
        if not isinstance(dati, dict):
            raise Errore(400, "Il corpo deve essere un oggetto JSON.")
        return dati

    def _post(self, percorso, query):
        if percorso not in POST_API:
            if percorso in TIPI_STATICI or re.match(r"/(api/(stato|show/)|copertine/|miniature/|qr/)", percorso):
                raise Errore(405, "Metodo non consentito.")
            raise Errore(404, "Non trovato.")
        if percorso == "/api/copertina":
            return self._copertina(query)
        corpo = self._json_corpo()
        gestore = getattr(self, POST_API[percorso])
        if percorso == "/api/anteprima":  # lenta: non si tiene il blocco
            return gestore(corpo)
        with self.server.blocco:  # controllo "libero" e avvio/scrittura insieme
            gestore(corpo)

    def _avvia(self, args):
        self.server.lancia(args)
        self._json(202, {"avviato": True})

    def _p_aggiorna(self, corpo):
        self.server.esige_libero()
        self._avvia([])

    def _p_aggiungi(self, corpo):
        link = _link(corpo)
        args = ["--add", link, *_argomenti_regole(corpo)]
        self.server.esige_libero()
        self._avvia(args)

    def _p_anteprima(self, corpo):
        link = _link(corpo)
        args = ["--preview", link, *_argomenti_regole(corpo)]
        try:
            codice, uscita = self.server.esecutore(args, 120)
            dati = json.loads(uscita)
            if not isinstance(dati, dict):
                raise ValueError
        except Exception:
            codice, dati = None, None
        if codice == 0:
            return self._json(200, dati)
        if codice == 1 and isinstance(dati.get("errore"), str):
            return self._json(422, dati)
        self._json(502, {"errore": "L'anteprima non è riuscita. Riprova."})

    def _p_rimuovi(self, corpo):
        slug = corpo.get("slug")
        _canale(_cfg(self.server.radice), slug)
        self.server.esige_libero()
        self._avvia(["--remove", slug])

    def _p_singolo(self, corpo):
        ident = _id_video(corpo)
        self.server.esige_libero()
        self._avvia(["--video", "https://www.youtube.com/watch?v=" + ident])

    def _p_riprova(self, corpo):
        radice = self.server.radice
        slug = corpo.get("slug")
        _canale(_cfg(radice), slug)
        ident = _id_video(corpo)
        self.server.esige_libero()
        with blocco_reale(radice):
            st = _stato_show(radice, slug)
            if _azzera_tentativi(st, ident):
                _scrivi_json(_percorso_stato(radice, slug), st)
        # il blocco e' libero: e' il lavoro lanciato a doverlo prendere
        self._avvia(["--only", slug])

    def _p_aggiorna_programma(self, corpo):
        self.server.esige_libero()
        self._avvia(["--aggiorna-programma"])

    def _p_apri_cartella(self, corpo):
        subprocess.run(["open", self.server.radice], timeout=10)
        self._json(200, {"aperto": True})

    def _copertina(self, query):
        radice = self.server.radice
        slug = (query.get("slug") or [""])[0]
        _canale(_cfg(radice), slug)
        tipo = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        estensione = TIPI_IMMAGINE.get(tipo)
        if estensione is None:
            raise Errore(415, "Formato immagine non supportato.")
        dati = self._leggi_corpo(MAX_IMMAGINE)
        if not dati:
            raise Errore(400, "Immagine vuota.")
        percorso = os.path.join(radice, "run", f"copertina-{slug}.{estensione}")
        with self.server.blocco:
            self.server.esige_libero()
            _scrivi_atomico(percorso, dati)
            self._avvia(["--cover", slug, percorso])

    def _p_regole(self, corpo):
        radice = self.server.radice
        slug = corpo.get("slug")
        _canale(_cfg(radice), slug)
        modifiche = _regole_valide({k: v for k, v in corpo.items() if k != "slug"})
        self.server.esige_libero()
        with self.server.blocco, blocco_reale(radice):
            cfg = _cfg(radice)
            c = _canale(cfg, slug)
            for chiave, v in modifiche.items():
                if v is None:
                    c.pop(chiave, None)
                else:
                    c[chiave] = v
            _scrivi_json(os.path.join(radice, "channels.json"), cfg)
            st = _stato_show(radice, slug)
            info = st.get("skipped_info")
            if isinstance(info, dict):
                da_togliere = {i for i, x in info.items()
                               if isinstance(x, dict) and x.get("kind") == "regola"}
                if da_togliere:
                    st["skipped"] = [i for i in st.get("skipped", [])
                                     if i not in da_togliere]
                    for i in da_togliere:
                        info.pop(i)
                    _scrivi_json(_percorso_stato(radice, slug), st)
        self._json(200, {"salvato": True, "regole": {k: c[k] for k in CHIAVI_REGOLE if k in c}})

    def _p_impostazioni(self, corpo):
        radice = self.server.radice
        modifiche = {}
        for chiave, v in corpo.items():
            if chiave == "max_episodes":
                modifiche[chiave] = _intero(v, 1, 50)
            elif chiave in ("min_duration_minutes", "max_duration_minutes"):
                modifiche[chiave] = _intero(v)
            elif chiave == "qualita":
                if v not in QUALITA:
                    raise Errore(400, "Qualità non valida.")
                modifiche["audio_quality"] = QUALITA[v]
            elif chiave in ("sponsorblock", "loudnorm", "chapters"):
                modifiche[chiave] = _booleano(v)
            else:
                raise Errore(400, "Impostazione sconosciuta.")
        self.server.esige_libero()
        with self.server.blocco, blocco_reale(radice):
            cfg = _cfg(radice)
            cfg.update(modifiche)
            _scrivi_json(os.path.join(radice, "channels.json"), cfg)
        self._json(200, {"salvato": True})


POST_API = {
    "/api/aggiorna": "_p_aggiorna", "/api/aggiungi": "_p_aggiungi",
    "/api/anteprima": "_p_anteprima", "/api/rimuovi": "_p_rimuovi",
    "/api/singolo": "_p_singolo", "/api/riprova": "_p_riprova",
    "/api/aggiorna-programma": "_p_aggiorna_programma",
    "/api/apri-cartella": "_p_apri_cartella", "/api/copertina": None,
    "/api/regole": "_p_regole", "/api/impostazioni": "_p_impostazioni"}


def main():
    server = crea_server(RADICE, PORTA)
    avvia_sorveglianza(RADICE)
    print(f"Pannello su http://127.0.0.1:{PORTA}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
