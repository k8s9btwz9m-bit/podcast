"""Stato del giro: blocco, avanzamento, riepilogo e avvisi.

Tutte le funzioni prendono la radice del progetto come primo argomento,
cosi' i test usano una cartella temporanea.
"""
import contextlib
import fcntl
import json
import os
import re
import shutil
import subprocess
import time


class Occupato(Exception):
    """Un altro giro tiene il blocco e l'attesa e' scaduta."""


def _percorso_blocco(radice):
    return os.path.join(radice, ".in-corso.lock")


def _pid_vivo(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return False
    return True


def _leggi_pid(blocco):
    """Pid scritto nel blocco, o None se manca, e' vuoto o non numerico."""
    try:
        with open(os.path.join(blocco, "pid")) as f:
            testo = f.read().strip()
        pid = int(testo)
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def in_corso(radice):
    """True se un processo vivo tiene il blocco."""
    pid = _leggi_pid(_percorso_blocco(radice))
    return pid is not None and _pid_vivo(pid)


class Blocco:
    """Gestore di contesto: cartella .in-corso.lock con file pid."""

    def __init__(self, radice, attesa_s=1800, passo_s=5):
        self.blocco = _percorso_blocco(radice)
        self.guardia = os.path.join(radice, ".in-corso.guardia")
        self.attesa_s = attesa_s
        self.passo_s = passo_s

    def _pulisci_avanzi(self):
        """Rimuove le cartelle private di acquisizioni interrotte (pid morto)."""
        base = os.path.basename(self.blocco) + "."
        cartella = os.path.dirname(self.blocco)
        for nome in os.listdir(cartella):
            resto = nome[len(base):]
            if nome.startswith(base) and resto.isdigit() \
                    and not _pid_vivo(int(resto)):
                shutil.rmtree(os.path.join(cartella, nome), ignore_errors=True)

    def _rimuovi_orfano(self):
        """Sposta il blocco orfano con un nome unico e poi lo cancella."""
        stale = "%s.stale.%d.%d" % (self.blocco, os.getpid(), time.time_ns())
        try:
            os.rename(self.blocco, stale)
        except OSError:
            return
        shutil.rmtree(stale, ignore_errors=True)

    @contextlib.contextmanager
    def _guardia(self):
        """Serializza controllo e acquisizione/rilascio (pochi millisecondi);
        non viene mai tenuta durante l'attesa o il lavoro."""
        fd = os.open(self.guardia, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)  # chiudere rilascia anche il flock

    def _prova(self):
        """Un tentativo di acquisizione, sotto la guardia. Si prepara una
        cartella privata con il pid gia' dentro e la si rinomina in
        .in-corso.lock: il blocco non esiste mai senza pid."""
        with self._guardia():
            self._pulisci_avanzi()
            pid = _leggi_pid(self.blocco)
            if os.path.exists(self.blocco):
                if pid is not None and _pid_vivo(pid):
                    return False
                self._rimuovi_orfano()
            privata = "%s.%d" % (self.blocco, os.getpid())
            shutil.rmtree(privata, ignore_errors=True)
            os.mkdir(privata)
            try:
                with open(os.path.join(privata, "pid"), "w") as f:
                    f.write(str(os.getpid()))
                os.rename(privata, self.blocco)
                return True
            except OSError:
                return False
            finally:
                shutil.rmtree(privata, ignore_errors=True)

    def __enter__(self):
        scadenza = time.monotonic() + self.attesa_s
        avvisato = False
        while not self._prova():
            if time.monotonic() >= scadenza:
                raise Occupato("un altro aggiornamento e' in corso")
            if not avvisato:
                avvisato = True
                print("C'è già un aggiornamento in corso: aspetto che finisca "
                      f"(fino a {max(1, round(self.attesa_s / 60))} minuti)…",
                      flush=True)
            time.sleep(self.passo_s)
        return self

    def __exit__(self, *args):
        with self._guardia():
            shutil.rmtree(self.blocco, ignore_errors=True)
        return False


def _cartella_run(radice):
    cartella = os.path.join(radice, "run")
    os.makedirs(cartella, exist_ok=True)
    return cartella


def _scrivi_json(percorso, dati):
    tmp = percorso + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False, indent=2)
    os.replace(tmp, percorso)


def scrivi_avanzamento(radice, d):
    _scrivi_json(os.path.join(_cartella_run(radice), "avanzamento.json"), d)


def leggi_avanzamento(radice):
    try:
        with open(os.path.join(radice, "run", "avanzamento.json"),
                  encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def esito_giro(completato, tentati, falliti):
    if not completato or (tentati > 0 and falliti == tentati):
        return "fallito"
    if falliti > 0:
        return "problemi"
    return "riuscito"


def _togli_avanzamento(cartella):
    try:
        os.remove(os.path.join(cartella, "avanzamento.json"))
    except FileNotFoundError:
        pass


def chiudi_giro(radice, riepilogo, aggiorna_riuscito=True):
    """Scrive il riepilogo del giro. ultimo-riuscito.json (che alimenta la
    sorveglianza delle 36 ore) cambia solo se `aggiorna_riuscito` e l'esito
    non e' "fallito": un comando che non scarica nulla non conta."""
    cartella = _cartella_run(radice)
    _scrivi_json(os.path.join(cartella, "ultimo-giro.json"), riepilogo)
    _togli_avanzamento(cartella)
    if aggiorna_riuscito and riepilogo.get("esito") != "fallito":
        _scrivi_json(os.path.join(cartella, "ultimo-riuscito.json"),
                     {"fine": riepilogo.get("fine")})


def scrivi_ultimo_comando(radice, azione, inizio, fine, messaggio):
    """Un comando finito male fuori da un giro: il pannello lo mostra come
    avviso, senza toccare il riepilogo dell'ultimo giro."""
    cartella = _cartella_run(radice)
    _scrivi_json(os.path.join(cartella, "ultimo-comando.json"),
                 {"azione": azione, "inizio": inizio, "fine": fine,
                  "messaggio": messaggio})
    _togli_avanzamento(cartella)


# (codice, schema, messaggio, rimedio). L'ordine conta: la rete prima di tutto
# (un errore di rete di yt-dlp porta comunque la sigla "[youtube]"), poi git.
_ERRORI = [
    ("rete",
     r"urlerror|urlopen error|timed out|network is unreachable|network is down"
     r"|could not resolve host|nodename nor servname|errno 8\b|no route to host"
     r"|name resolution|connection refused|connection reset",
     "Manca la connessione a internet",
     "Controlla la rete e riprova: il prossimo giro riparte da solo."),
    ("git",
     r"git push|git commit|authentication failed|! \[rejected\]"
     r"|non-fast-forward|fetch first|non è collegata a github",
     "Non sono riuscito a pubblicare le modifiche",
     "Riprova al prossimo giro; se persiste, controlla l'accesso a GitHub."),
    ("disco",
     r"no space left|errno 28|disk full",
     "Il disco e' pieno",
     "Libera spazio sul Mac e riprova."),
    ("youtube",
     r"sign in to confirm|http error (403|429)|\bnsig\b|unable to extract"
     r"|requested format is not available|video unavailable|private video"
     r"|members-only",
     "YouTube ha rifiutato dei download",
     "Di solito basta aggiornare il programma che scarica i video."),
]


def traduci_errore(testo):
    for codice, schema, messaggio, rimedio in _ERRORI:
        if re.search(schema, testo or "", re.IGNORECASE):
            return {"codice": codice, "messaggio": messaggio, "rimedio": rimedio}
    return {"codice": "sconosciuto",
            "messaggio": "Qualcosa non ha funzionato",
            "rimedio": "Guarda il registro del giro per i dettagli."}


def notifica(titolo, testo):
    """Notifica di sistema, come in da-shortcut.sh. Non solleva mai."""
    try:
        subprocess.run(
            ["osascript",
             "-e", "on run {t, m}",
             "-e", "display notification m with title t",
             "-e", "end run", titolo, testo],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass
