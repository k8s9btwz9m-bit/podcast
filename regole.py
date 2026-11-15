"""Regole di selezione e riconoscimento dei link: funzioni pure, senza rete."""
import re
from urllib.parse import parse_qs, urlparse

ID_VIDEO = re.compile(r"^[A-Za-z0-9_-]{11}$")
ID_CANALE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


# link incollati senza "https://": solo per gli indirizzi noti di YouTube
SENZA_SCHEMA = re.compile(
    r"^(?:(?:www\.|m\.)?youtube\.com|youtu\.be)(?:[/?#]|$)", re.IGNORECASE)


def _risultato(tipo, ident, url):
    return {"tipo": tipo, "id": ident, "url": url}


def classifica_link(testo):
    """Riconosce cosa è un link: canale, playlist, video o sconosciuto. Non solleva mai."""
    try:
        url = (testo or "").strip()
        if ID_CANALE.match(url):
            return _risultato("canale", url, url)
        if SENZA_SCHEMA.match(url):
            url = "https://" + url
        p = urlparse(url)
        host = (p.hostname or "").lower()
        segmenti = [s for s in p.path.split("/") if s]
        q = parse_qs(p.query)
        if host == "youtu.be":
            if segmenti and ID_VIDEO.match(segmenti[0]):
                return _risultato("video", segmenti[0], url)
        elif host == "youtube.com" or host.endswith(".youtube.com"):
            v = (q.get("v") or [""])[0]
            lista = (q.get("list") or [""])[0]
            if segmenti[:1] == ["watch"] and ID_VIDEO.match(v):
                return _risultato("video", v, url)
            if segmenti[:1] in (["shorts"], ["live"], ["embed"]) and len(segmenti) > 1 \
                    and ID_VIDEO.match(segmenti[1]):
                return _risultato("video", segmenti[1], url)
            if lista and (segmenti[:1] in (["playlist"], ["watch"])):
                return _risultato("playlist", lista, url)
            if segmenti[:1] == ["channel"] and len(segmenti) > 1 and ID_CANALE.match(segmenti[1]):
                return _risultato("canale", segmenti[1], url)
            if segmenti and segmenti[0].startswith("@"):
                return _risultato("canale", None, url)
        return _risultato("sconosciuto", None, url)
    except Exception:
        return _risultato("sconosciuto", None, str(testo or "").strip())


def parole(testo):
    """Separa per virgola, toglie spazi e vuoti, minuscole."""
    return [p.strip().lower() for p in (testo or "").split(",") if p.strip()]


def _elenco(valore):
    if isinstance(valore, (list, tuple)):
        return parole(",".join(str(v) for v in valore))
    return parole(valore)


def regole_effettive(cfg, channel):
    """Regole del canale, con ricaduta sulle generali."""
    def scelta(chiave, predefinito=None):
        if chiave in channel:
            return channel[chiave]
        return cfg.get(chiave, predefinito)

    if channel.get("manual") or channel.get("type") == "playlist":
        min_s = max_s = 0
    else:
        min_s = int(scelta("min_duration_minutes", 2)) * 60
        max_s = int(scelta("max_duration_minutes", 120)) * 60
    return {
        "min_s": min_s,
        "max_s": max_s,
        "solo": _elenco(scelta("title_include", "")),
        "salta": _elenco(scelta("title_exclude", "")),
        "sponsor": bool(scelta("sponsorblock", True)),
    }


def valuta(regole, titolo, durata_s):
    """(entra, motivo). Con durata sconosciuta la durata non si giudica."""
    if durata_s is not None:
        if regole["min_s"] and durata_s < regole["min_s"]:
            return False, f"più corto di {regole['min_s'] // 60} min"
        if regole["max_s"] and durata_s > regole["max_s"]:
            return False, f"più lungo di {regole['max_s'] // 60} min"
    t = (titolo or "").casefold()
    for p in regole["salta"]:
        if p.casefold() in t:
            return False, f"titolo con: {p}"
    if regole["solo"] and not any(p.casefold() in t for p in regole["solo"]):
        return False, f"titolo senza: {regole['solo'][0]}"
    return True, ""
