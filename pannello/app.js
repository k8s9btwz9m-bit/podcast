"use strict";
/* Pannello di controllo: parla solo con il server locale (pannello.py).
   Tutti i testi che arrivano dai dati entrano nella pagina con textContent. */
(function () {
  const $ = (id) => document.getElementById(id);
  const MESI = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"];
  const CHIAVE_SHOW = "podcast-pannello-show";
  const NOTA_PREDEFINITA = "Lo riconosco da solo e ti mostro cosa entrerebbe, prima di aggiungere.";
  const MSG_OCCUPATO = "C'è già un lavoro in corso.";

  // ------------------------------------------------------------ utilità
  function el(tag, attrs, ...figli) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === false || v == null) continue;
      if (k === "class") e.className = v;
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const f of figli) {
      if (f == null || f === false) continue;
      e.append(f instanceof Node ? f : document.createTextNode(String(f)));
    }
    return e;
  }

  function icona(id, classe) {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("class", "i" + (classe ? " " + classe : ""));
    s.setAttribute("aria-hidden", "true");
    const u = document.createElementNS("http://www.w3.org/2000/svg", "use");
    u.setAttribute("href", "#" + id);
    s.append(u);
    return s;
  }

  function testo(nodo, valore) {
    if (nodo.textContent !== valore) nodo.textContent = valore;
  }

  const pad = (n) => String(n).padStart(2, "0");
  const hhmm = (d) => pad(d.getHours()) + ":" + pad(d.getMinutes());
  const stessoGiorno = (a, b) => a.toDateString() === b.toDateString();

  function quando(d, adesso) {
    adesso = adesso || new Date();
    if (stessoGiorno(d, adesso)) return "oggi alle " + hhmm(d);
    const ieri = new Date(adesso);
    ieri.setDate(ieri.getDate() - 1);
    if (stessoGiorno(d, ieri)) return "ieri alle " + hhmm(d);
    return "il " + d.getDate() + " " + MESI[d.getMonth()] + " alle " + hhmm(d);
  }

  function durata(s) {
    if (s == null || s === "" || isNaN(Number(s))) return "";
    s = Math.round(Number(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    return h > 0 ? h + ":" + pad(m) + ":" + pad(r) : m + ":" + pad(r);
  }

  function dataBreve(iso) {
    const d = new Date(iso);
    return isNaN(d) ? "" : d.getDate() + " " + MESI[d.getMonth()];
  }

  const plurale = (n, uno, molti) => n + " " + (n === 1 ? uno : molti);

  function leggiJson(chiave, predefinito) {
    try {
      const v = window.localStorage.getItem(chiave);
      return v == null ? predefinito : v;
    } catch (e) {
      return predefinito;
    }
  }

  function scriviLocale(chiave, valore) {
    try { window.localStorage.setItem(chiave, valore); } catch (e) { /* senza memoria locale si va avanti */ }
  }

  // Firme: si ridisegna solo quello che è cambiato davvero.
  const firme = {};
  function cambiato(chiave, valore) {
    const f = JSON.stringify(valore);
    if (firme[chiave] === f) return false;
    firme[chiave] = f;
    return true;
  }

  // ------------------------------------------------------------ avvisi
  let tToast = null;
  function avvisa(msg, errore) {
    const t = $("toast");
    t.textContent = msg;
    t.classList.add("on");
    clearTimeout(tToast);
    tToast = setTimeout(() => t.classList.remove("on"), errore ? 6500 : 3200);
  }

  let ultimoAnnuncio = "";
  function annuncia(msg) {
    if (msg === ultimoAnnuncio) return;
    ultimoAnnuncio = msg;
    $("annunci").textContent = msg;
  }

  function segnalaOffline(giu) {
    $("offline").hidden = !giu;
  }

  // ------------------------------------------------------------ rete
  async function api(percorso, opzioni) {
    opzioni = opzioni || {};
    const metodo = opzioni.metodo || "GET";
    const init = { method: metodo, cache: "no-store", headers: {} };
    if (metodo === "POST") {
      init.headers["X-Podcast"] = "1";
      if (opzioni.file) {
        init.headers["Content-Type"] = opzioni.file.type;
        init.body = opzioni.file;
      } else {
        init.headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(opzioni.corpo || {});
      }
    }
    let r;
    try {
      r = await fetch(percorso, init);
    } catch (e) {
      segnalaOffline(true);
      return null;
    }
    segnalaOffline(false);
    let dati = null;
    try { dati = await r.json(); } catch (e) { /* risposta senza corpo */ }
    return { ok: r.ok, stato: r.status, dati: dati };
  }

  function frase(r) {
    return (r.stato === 409 && MSG_OCCUPATO) ||
      (r.dati && r.dati.errore) || "Qualcosa non ha funzionato.";
  }

  // Un'azione sul server: avvisa dell'esito e ricarica subito lo stato.
  async function azione(percorso, corpo, messaggioOk) {
    const r = await api(percorso, { metodo: "POST", corpo: corpo });
    if (!r) return null;
    if (r.ok) {
      if (messaggioOk) avvisa(messaggioOk);
    } else {
      avvisa(frase(r), true);
    }
    rapidoFino = Date.now() + 15000;
    aggiornaStato();
    return r;
  }

  // ------------------------------------------------------------ stato
  let S = null;
  let timer = null;
  let inVolo = false;
  let ripeti = false;
  let rapidoFino = 0;
  let eraInCorso = false;
  let versioneCopertine = 0;
  let coverAttesa = false;

  async function aggiornaStato() {
    if (inVolo) { ripeti = true; return; }
    inVolo = true;
    let ok = false;
    try {
      const r = await api("/api/stato");
      if (r && r.ok && r.dati) {
        ok = true;
        S = r.dati;
        if (!S.in_corso && (eraInCorso || coverAttesa)) {
          versioneCopertine = Date.now();
          coverAttesa = false;
        }
        eraInCorso = S.in_corso;
        disegna();
        await aggiornaScheda();
      } else if (r) {
        avvisa(frase(r), true);
      }
    } catch (e) {
      console.error(e);
      ok = true;  // un errore nel disegno non deve fermare il controllo periodico
    } finally {
      inVolo = false;
    }
    pianifica(ok);
    if (ripeti) { ripeti = false; aggiornaStato(); }
  }

  function pianifica(ok) {
    clearTimeout(timer);
    const veloce = S && (S.in_corso || Date.now() < rapidoFino);
    timer = setTimeout(aggiornaStato, !ok ? 5000 : veloce ? 2000 : 30000);
  }

  function disegna() {
    disegnaTestata();
    disegnaScala();
    disegnaAvanzamento();
    disegnaErrore();
    disegnaComando();
    sistemaSelezione();
    disegnaScaffale();
    disegnaSpazio();
    aggiornaImpostazioni();
    disegnaSaltatiGiro();
  }

  // ------------------------------------------------------------ testata
  function descriviStato() {
    const g = S.ultimo_giro;
    const prossimo = "Prossimo giro alle " + S.prossimo_giro + ".";
    if (S.in_corso) {
      const a = S.avanzamento;
      const inizio = a && a.inizio ? new Date(a.inizio) : null;
      return { classe: "busy", titolo: "Aggiornamento in corso.",
               riga: inizio && !isNaN(inizio) ? "Iniziato alle " + hhmm(inizio) + "." : "" };
    }
    if (g && g.esito === "fallito") {
      const inizio = new Date(g.inizio);
      return { classe: "bad", titolo: "Il giro delle " + (isNaN(inizio) ? "?" : hhmm(inizio)) + " non è riuscito.",
               riga: prossimo };
    }
    if (S.fermo_da_36_ore) {
      if (!S.ultimo_riuscito && !g) return { classe: "idle", titolo: "Ancora nessun giro.", riga: prossimo };
      return { classe: "bad", titolo: "Nessun giro riuscito da più di 36 ore.", riga: prossimo };
    }
    if (g && g.esito === "problemi") {
      let n = 0;
      for (const s of g.show || []) n += Number(s.falliti) || 0;
      if (!n) n = (g.errori || []).length || 1;
      return { classe: "", titolo: "In pari, con " + plurale(n, "problema", "problemi") + ".", riga: prossimo };
    }
    const r = S.ultimo_riuscito ? new Date(S.ultimo_riuscito) : null;
    return { classe: "", titolo: "In pari." + (r && !isNaN(r) ? " Aggiornato " + quando(r) + "." : ""),
             riga: prossimo };
  }

  function disegnaTestata() {
    const d = descriviStato();
    const p = $("stato");
    p.className = "status" + (d.classe ? " " + d.classe : "");
    testo($("stato-titolo"), d.titolo);
    testo($("stato-riga"), d.riga);
    annuncia(d.titolo + " " + d.riga);
    const b = $("aggiorna");
    b.disabled = !!S.in_corso;
    testo($("aggiorna-testo"), S.in_corso ? "Sto aggiornando…" : "Aggiorna adesso");
    const use = $("aggiorna-icona").firstElementChild;
    use.setAttribute("href", S.in_corso ? "#i-spin" : "#i-refresh");
    $("aggiorna-icona").classList.toggle("spin", !!S.in_corso);
  }

  const percentuale = (d) => (d.getHours() * 60 + d.getMinutes()) / 1440 * 100;

  function disegnaScala() {
    if (!S) return;
    const ora = new Date();
    const adesso = $("scala-adesso");
    const pc = percentuale(ora);
    adesso.style.left = pc + "%";
    adesso.setAttribute("data-t", "ADESSO " + hhmm(ora));
    adesso.classList.toggle("late", pc > 78);
    let etichetta = "Giornata di oggi: giri automatici alle 8 e alle 20, adesso sono le " + hhmm(ora) + ".";

    const g = S.ultimo_giro;
    const fine = g && g.fine ? new Date(g.fine) : null;
    const ultimo = $("scala-ultimo");
    if (fine && !isNaN(fine) && stessoGiorno(fine, ora)) {
      const fallito = g.esito === "fallito";
      ultimo.hidden = false;
      ultimo.className = "run" + (fallito ? " fail" : "");
      ultimo.style.left = percentuale(fine) + "%";
      const t = "Giro delle " + hhmm(fine) + (fallito ? " fallito" : g.esito === "problemi" ? " con problemi" : " riuscito");
      ultimo.setAttribute("title", t);
      etichetta += " " + t + ".";
    } else {
      ultimo.hidden = true;
    }

    const m = /^(\d{1,2}):(\d{2})$/.exec(S.prossimo_giro || "");
    const prossimo = $("scala-prossimo");
    if (m) {
      const minuti = Number(m[1]) * 60 + Number(m[2]);
      const domani = minuti <= ora.getHours() * 60 + ora.getMinutes();
      prossimo.hidden = false;
      prossimo.style.left = minuti / 1440 * 100 + "%";
      const t = "Prossimo giro " + (domani ? "domani " : "") + "alle " + S.prossimo_giro;
      prossimo.setAttribute("title", t);
      etichetta += " " + t + ".";
    } else {
      prossimo.hidden = true;
    }
    $("scala").setAttribute("aria-label", etichetta);
  }

  function disegnaAvanzamento() {
    const box = $("avanzamento");
    box.hidden = !S.in_corso;
    if (!S.in_corso) return;
    const a = S.avanzamento;
    testo($("avanzamento-testo"), a && a.testo ? a.testo : "Sto lavorando…");
    let riga = "";
    if (a && a.titolo) riga = a.titolo;
    if (a && a.show_n && a.show_tot) riga += (riga ? " · " : "") + "show " + a.show_n + " di " + a.show_tot;
    testo($("avanzamento-show"), riga);
    const barra = $("avanzamento-barra");
    if (a && a.show_tot > 0 && a.show_n > 0) {
      const dentro = a.video_tot > 0 ? Math.max(0, a.video_n - 1) / a.video_tot : 0;
      const pc = Math.max(3, Math.min(100, ((a.show_n - 1) + dentro) / a.show_tot * 100));
      barra.classList.remove("indet");
      barra.setAttribute("aria-valuenow", String(Math.round(pc)));
      barra.firstElementChild.style.width = pc + "%";
    } else {
      barra.classList.add("indet");
      barra.removeAttribute("aria-valuenow");
      barra.firstElementChild.style.width = "";
    }
  }

  function disegnaErrore() {
    const g = S.ultimo_giro;
    const box = $("errore-giro");
    const visibile = !S.in_corso && g && g.esito === "fallito";
    box.hidden = !visibile;
    if (!visibile) { firme.errore = null; return; }
    const errori = Array.isArray(g.errori) && g.errori.length ? g.errori
      : [{ codice: "sconosciuto", messaggio: "Qualcosa non ha funzionato",
           rimedio: "Guarda il registro del giro per i dettagli." }];
    if (!cambiato("errore", errori)) return;
    testo($("errore-titolo"), errori[0].messaggio);
    const contenitore = $("errore-testi");
    contenitore.replaceChildren(el("p", null, errori[0].rimedio));
    for (const e of errori.slice(1)) contenitore.append(el("p", null, e.messaggio + ". " + e.rimedio));
    $("errore-programma").hidden = !errori.some((e) => e.codice === "youtube");
  }

  // Un comando (aggiungi, rimuovi, copertina...) finito male fuori dal giro:
  // un avviso a parte, che non cambia la testata ne' la scala delle ore.
  // Si chiude e non ricompare finche' il comando fallito e' lo stesso.
  let comandoChiuso = null;
  function disegnaComando() {
    const c = S.ultimo_comando;
    const visibile = !!c && c.fine !== comandoChiuso;
    $("comando-fallito").hidden = !visibile;
    if (visibile) testo($("comando-testo"), "Non riuscito: " + c.messaggio);
  }
  $("comando-chiudi").addEventListener("click", () => {
    if (S && S.ultimo_comando) comandoChiuso = S.ultimo_comando.fine;
    $("comando-fallito").hidden = true;
  });

  $("aggiorna").addEventListener("click", () => azione("/api/aggiorna", {}, "Sto aggiornando."));
  $("errore-programma").addEventListener("click", () =>
    azione("/api/aggiorna-programma", {}, "Aggiorno il programma e riprovo."));
  $("apri-cartella").addEventListener("click", () => azione("/api/apri-cartella", {}));

  // ------------------------------------------------------------ scaffale
  let sel = null;

  function sistemaSelezione() {
    const slugs = S.show.map((s) => s.slug);
    if (sel && slugs.includes(sel)) return;
    const salvato = leggiJson(CHIAVE_SHOW, null);
    const nuovo = slugs.includes(salvato) ? salvato : slugs[0] || null;
    if (nuovo !== sel) cambiaSelezione(nuovo);
  }

  function cambiaSelezione(slug) {
    sel = slug;
    dettaglio = null;
    firme.eps = firme.side = firme.saltatiShow = null;
    mostraConferma(false);
    if (slug) scriviLocale(CHIAVE_SHOW, slug);
    if (!slug) $("scheda").hidden = true;
  }

  function disegnaScaffale() {
    const tot = S.show.reduce((a, s) => ({ ep: a.ep + s.episodi, mb: a.mb + s.mb }), { ep: 0, mb: 0 });
    testo($("scaffale-somma"), S.show.length
      ? plurale(S.show.length, "show", "show") + " · " + plurale(tot.ep, "episodio", "episodi") + " · " + tot.mb + " MB"
      : "");
    const ul = $("scaffale");
    const inCorso = S.in_corso && S.avanzamento ? S.avanzamento.show : null;
    const firma = S.show.map((s) => [s.slug, s.title, s.tipo, s.episodi, s.mb, s.nuovi_oggi, s.slug === inCorso]);
    if (cambiato("scaffale", [firma, versioneCopertine])) {
      const fuoco = document.activeElement && ul.contains(document.activeElement)
        ? document.activeElement.getAttribute("data-slug") : null;
      ul.replaceChildren(...S.show.map((s) => tessera(s, s.slug === inCorso)));
      if (!S.show.length) {
        ul.replaceChildren(el("li", { class: "empty" }, "Ancora nessuno show. Incolla qui sopra il link di un canale, di una playlist o di un video."));
      }
      if (fuoco) {
        const b = ul.querySelector('[data-slug="' + CSS.escape(fuoco) + '"]');
        if (b) b.focus();
      }
    }
    for (const b of ul.querySelectorAll(".tile")) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-slug") === sel));
    }
    posizionaTacca();
  }

  function urlCopertina(slug) {
    return "/copertine/" + encodeURIComponent(slug) + ".jpg" +
      (versioneCopertine ? "?v=" + versioneCopertine : "");
  }

  // Una copertina tonda su quadrato bianco (anche con un margine bianco
  // intorno al disco) si ingrandisce dentro il riquadro ritagliato, in
  // misura del diametro reale del disco. Un logo su fondo bianco no.
  const tonde = new Map();  // url -> fattore di ingrandimento, o null
  function misuraTonda(img) {
    const N = 96;
    const c = document.createElement("canvas");
    c.width = c.height = N;
    const x = c.getContext("2d", { willReadFrequently: true });
    x.drawImage(img, 0, 0, N, N);
    const dati = x.getImageData(0, 0, N, N).data;
    const bianco = (px, py) => {
      const i = (py * N + px) * 4;
      return dati[i + 3] < 16 || (dati[i] >= 235 && dati[i + 1] >= 235 && dati[i + 2] >= 235);
    };
    if (![[0, 0], [N - 1, 0], [0, N - 1], [N - 1, N - 1]].every((p) => bianco(p[0], p[1]))) return null;
    let scuri = 0;
    for (let k = 0; k < 8; k++) {
      const a = k * Math.PI / 4;
      const px = Math.round(N / 2 + Math.cos(a) * 0.36 * N), py = Math.round(N / 2 + Math.sin(a) * 0.36 * N);
      if (!bianco(Math.min(N - 1, Math.max(0, px)), Math.min(N - 1, Math.max(0, py)))) scuri++;
    }
    if (scuri < 7) return null;
    const m = N / 2;
    let sx = 0, dx = N - 1, sy = 0, dy = N - 1;
    while (sx < N && bianco(sx, m)) sx++;
    while (dx >= 0 && bianco(dx, m)) dx--;
    while (sy < N && bianco(m, sy)) sy++;
    while (dy >= 0 && bianco(m, dy)) dy--;
    const d = Math.min(dx - sx + 1, dy - sy + 1) / N;
    if (!(d >= 0.6 && d <= 1)) return null;
    return Math.min(1.75, Math.max(1.3, 1.414 / d * 1.01));
  }

  function controllaTonda(img) {
    const url = img.getAttribute("src");
    try {
      if (!tonde.has(url)) tonde.set(url, misuraTonda(img));
    } catch (e) {
      return;  // lettura non riuscita: l'immagine resta com'è
    }
    const zoom = tonde.get(url);
    img.classList.toggle("cerchio", zoom != null);
    if (zoom != null) img.style.setProperty("--zoom", zoom.toFixed(3));
  }

  function immagineCopertina(slug) {
    const img = el("img", { src: urlCopertina(slug), alt: "", loading: "lazy" });
    img.addEventListener("load", () => controllaTonda(img));
    return img;
  }

  function tessera(s, inCorso) {
    const arte = el("span", { class: "art" });
    const img = immagineCopertina(s.slug);
    img.addEventListener("error", () => {
      img.remove();
      arte.prepend(el("span", { class: "noimg" }, s.title));
    });
    arte.append(img);
    if (inCorso) arte.append(el("span", { class: "badge" }, "In corso"));
    else if (s.nuovi_oggi > 0) arte.append(el("span", { class: "badge" }, plurale(s.nuovi_oggi, "nuovo", "nuovi")));
    const unita = s.tipo === "singoli" ? plurale(s.episodi, "video singolo", "video singoli")
      : plurale(s.episodi, "episodio", "episodi");
    const b = el("button", { class: "tile", type: "button", "data-slug": s.slug, "aria-pressed": "false" },
      arte, el("span", { class: "name" }, s.title), el("span", { class: "sub" }, unita + " · " + s.mb + " MB"));
    b.addEventListener("click", () => {
      if (sel === s.slug) return;
      cambiaSelezione(s.slug);
      disegnaScaffale();
      aggiornaScheda();
    });
    return el("li", null, b);
  }

  // ------------------------------------------------------------ scheda
  // La tacca in cima alla scheda punta al centro della tessera selezionata;
  // dove la scheda non sta sotto la riga della tessera non si mostra.
  function posizionaTacca() {
    const scheda = $("scheda");
    const b = sel ? $("scaffale").querySelector('[data-slug="' + CSS.escape(sel) + '"]') : null;
    if (!b || scheda.hidden || window.innerWidth <= 720) {
      scheda.classList.add("senza-tacca");
      return;
    }
    const t = b.getBoundingClientRect(), s = scheda.getBoundingClientRect();
    const x = Math.max(28, Math.min(s.width - 28, t.left + t.width / 2 - s.left));
    scheda.style.setProperty("--tacca", x + "px");
    scheda.classList.toggle("senza-tacca", t.bottom < s.top - 90);
  }
  window.addEventListener("resize", posizionaTacca);

  let dettaglio = null;
  let confermaAperta = false;
  let saltatiAperti = false;
  let slugQr = null;

  async function aggiornaScheda() {
    if (!sel) return;
    const slug = sel;
    const r = await api("/api/show/" + encodeURIComponent(slug));
    if (slug !== sel) return;
    if (!r) return;
    if (r.stato === 404) { cambiaSelezione(null); sistemaSelezione(); return; }
    if (!r.ok) return;
    dettaglio = r.dati;
    disegnaScheda();
  }

  function datiShow() {
    return S && dettaglio ? S.show.find((s) => s.slug === dettaglio.slug) : null;
  }

  function disegnaScheda() {
    const d = dettaglio, s = datiShow();
    if (!d || !s) return;
    const scheda = $("scheda");
    scheda.hidden = false;
    scheda.setAttribute("aria-label", "Scheda di " + d.title);
    posizionaTacca();

    if (cambiato("side", [d.slug, d.title, d.feed_url])) {
      if (slugQr !== d.slug) {
        slugQr = d.slug;
        const img = el("img", { src: "/qr/" + encodeURIComponent(d.slug) + ".svg", alt: "Codice QR di " + d.title,
                                width: "35", height: "35" });
        img.addEventListener("error", () => {
          $("qr").replaceChildren(el("span", { class: "none", role: "img" }, "QR non disponibile"));
        });
        $("qr").replaceChildren(img);
      } else {
        const img = $("qr").querySelector("img");
        if (img) img.setAttribute("alt", "Codice QR di " + d.title);
      }
    }

    if (cambiato("eps", [d.title, s.episodi, s.mb, d.episodi, d.saltati.length, versioneCopertine, !!S.in_corso])) {
      testo($("scheda-titolo"), d.title);
      let somma = plurale(d.episodi.length, "episodio", "episodi") + " · " + s.mb + " MB";
      if (d.saltati.length) somma += " · " + plurale(d.saltati.length, "video saltato", "video saltati") + " finora";
      testo($("scheda-somma"), somma);
      const ol = $("episodi");
      if (d.episodi.length) {
        ol.replaceChildren(...d.episodi.map((e) => episodio(d.slug, e, !!S.in_corso)));
      } else {
        ol.replaceChildren(el("li", { class: "empty" }, "Nessun episodio ancora."));
      }
    }

    if (cambiato("saltatiShow", [d.slug, d.saltati])) {
      const det = $("saltati-show");
      det.hidden = d.saltati.length === 0;
      testo($("saltati-show-titolo"), "Tutti i saltati di questo show (" + d.saltati.length + ")");
      $("saltati-show-elenco").replaceChildren(...d.saltati.map((v) => voceSaltato(d.slug, v, null)));
      det.open = saltatiAperti && d.saltati.length > 0;
    }

    aggiornaRegole(d);
  }

  function episodio(slug, e, occupato) {
    // senza miniatura propria si mostra la copertina dello show
    const foto = e.image
      ? el("img", { src: "/miniature/" + encodeURIComponent(slug) + "/" + encodeURIComponent(e.id) + ".jpg",
                    alt: "", loading: "lazy" })
      : immagineCopertina(slug);
    foto.addEventListener("error", () => foto.remove());
    const img = el("span", { class: "th", "aria-hidden": "true" }, foto);
    const meta = el("span", { class: "m" }, el("span", null, dataBreve(e.published)));
    if (e.nuovo_oggi) meta.append(el("em", null, "nuovo oggi"));
    if (e.chapters > 0) meta.append(el("span", null, plurale(e.chapters, "capitolo", "capitoli")));
    if (e.tagliati_s > 0) meta.append(el("span", null, "tagliati " + durata(e.tagliati_s) + " di sponsor"));
    const elimina = el("button", { class: "ep-del", type: "button", title: "Elimina",
                                   "aria-label": "Elimina l'episodio " + e.title }, "×");
    elimina.disabled = !!occupato;
    elimina.addEventListener("click", () => chiediElimina(slug, e, elimina));
    return el("li", { class: "ep" }, img, el("span", { class: "t", title: e.title }, e.title),
      el("span", { class: "dur" }, durata(e.duration)), elimina, meta);
  }

  // Titolo (o id) di un video saltato: collegamento a YouTube solo con un id valido.
  const ID_VIDEO = /^[A-Za-z0-9_-]{11}$/;
  function titoloSaltato(v) {
    const nome = v.title || v.id || "";
    if (typeof v.id !== "string" || !ID_VIDEO.test(v.id)) return document.createTextNode(nome);
    const a = el("a", { href: "https://www.youtube.com/watch?v=" + v.id, target: "_blank",
                        rel: "noopener noreferrer" }, nome);
    if (!v.title) a.append(el("span", { class: "apri" }, "apri su YouTube"));
    return a;
  }

  // Un video saltato con il suo pulsante: "Riprova" se il download è fallito,
  // "Scarica tra i video singoli" se a fermarlo è stata una regola.
  function voceSaltato(slug, v, nomeShow) {
    const fallito = v.kind === "fallito";
    const b = el("button", { class: "btn small", type: "button" },
      fallito ? "Riprova" : "Scarica tra i video singoli");
    b.addEventListener("click", async () => {
      b.disabled = true;
      const r = fallito
        ? await azione("/api/riprova", { slug: slug, id: v.id }, "Riprovo a scaricarlo.")
        : await azione("/api/singolo", { id: v.id }, "Lo scarico tra i video singoli.");
      if (!r || !r.ok) b.disabled = false;
    });
    const perche = (nomeShow ? nomeShow + " · " : "") + (v.reason || "motivo non registrato");
    return el("li", null, el("div", { class: "t" }, titoloSaltato(v)),
      el("div", { class: "why" }, perche), b);
  }

  $("saltati-show").addEventListener("toggle", (ev) => { saltatiAperti = ev.target.open; });

  // copia, copertina, rimozione
  async function copiaTesto(valore) {
    try {
      await navigator.clipboard.writeText(valore);
      return true;
    } catch (e) {
      try {
        const t = el("textarea", { "aria-hidden": "true" });
        t.value = valore;
        t.style.cssText = "position:fixed;opacity:0;top:0;left:0";
        document.body.append(t);
        t.select();
        const ok = document.execCommand("copy");
        t.remove();
        return ok;
      } catch (e2) {
        return false;
      }
    }
  }

  $("copia").addEventListener("click", async () => {
    if (!dettaglio) return;
    if (await copiaTesto(dettaglio.feed_url)) avvisa("Indirizzo del feed copiato");
    else avvisa("Non riesco a copiare: l'indirizzo è " + dettaglio.feed_url, true);
  });

  $("copertina").addEventListener("click", () => $("copertina-file").click());
  $("copertina-file").addEventListener("change", async (ev) => {
    const input = ev.target;
    const file = input.files && input.files[0];
    input.value = "";
    if (!file || !dettaglio) return;
    if (!/^image\/(jpeg|png|webp|heic)$/.test(file.type)) {
      avvisa("Scegli un'immagine JPEG, PNG, WebP o HEIC.", true);
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      avvisa("L'immagine supera i 10 MB.", true);
      return;
    }
    const r = await api("/api/copertina?slug=" + encodeURIComponent(dettaglio.slug), { metodo: "POST", file: file });
    if (!r) return;
    if (r.ok) {
      avvisa("Cambio la copertina.");
      coverAttesa = true;
      versioneCopertine = Date.now();
    } else {
      avvisa(frase(r), true);
    }
    rapidoFino = Date.now() + 15000;
    aggiornaStato();
  });

  // La stessa conferma serve per lo show e per un episodio: `conferma` dice
  // cosa chiede, cosa fare se si accetta e a chi restituire il cursore.
  let conferma = null;

  function mostraConferma(si, voce) {
    confermaAperta = si;
    conferma = si ? voce : null;
    $("conferma").hidden = !si;
    $("azioni").hidden = si;
    if (si) {
      testo($("conferma-testo"), voce.testo);
      testo($("conferma-si-testo"), voce.etichetta);
      $("conferma-no").focus();
    }
  }

  function chiudiConferma() {
    const da = conferma && conferma.apertoDa;
    mostraConferma(false);
    const b = da && da.isConnected && !da.disabled ? da : $("rimuovi");
    if (!b.hidden) b.focus();
  }

  $("rimuovi").addEventListener("click", () => {
    if (!dettaglio) return;
    const slug = dettaglio.slug;
    mostraConferma(true, {
      testo: "Rimuovere " + dettaglio.title + "? Audio e feed di questo show vengono cancellati.",
      etichetta: "Rimuovi", apertoDa: $("rimuovi"),
      esegui: () => azione("/api/rimuovi", { slug: slug }, "Rimuovo lo show.") });
  });

  function chiediElimina(slug, e, bottone) {
    mostraConferma(true, {
      testo: "Eliminare «" + e.title + "»? L'audio viene cancellato e l'episodio non verrà più scaricato.",
      etichetta: "Elimina", apertoDa: bottone,
      esegui: () => azione("/api/rimuovi-episodio", { slug: slug, id: e.id }, "Elimino l'episodio.") });
  }

  $("conferma-no").addEventListener("click", chiudiConferma);
  $("conferma").addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") chiudiConferma();
  });
  $("conferma-si").addEventListener("click", async () => {
    if (!dettaglio || !conferma) return;
    const b = $("conferma-si");
    const voce = conferma;
    b.disabled = true;
    const r = await voce.esegui();
    b.disabled = false;
    if (r && r.ok) mostraConferma(false);
  });

  // ------------------------------------------------------------ moduli salvabili
  // Un modulo che l'utente sta compilando non si tocca mai: si riempie solo se
  // è pulito e nessun suo campo ha il cursore.
  function modulo(form, salva, esito) {
    const m = { sporco: false };
    m.segna = () => { m.sporco = true; salva.disabled = false; esito.textContent = ""; };
    m.pulito = () => { m.sporco = false; salva.disabled = true; };
    m.inUso = () => {
      const a = document.activeElement;
      return form.contains(a) && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName);
    };
    m.puoRiempire = () => !m.sporco && !m.inUso();
    m.dire = (msg, errore) => {
      esito.className = "saved" + (errore ? " bad" : "");
      esito.textContent = msg;
    };
    form.addEventListener("input", m.segna);
    return m;
  }

  function interruttore(b, valore) {
    if (valore === undefined) return b.getAttribute("aria-checked") === "true";
    b.setAttribute("aria-checked", String(!!valore));
    return !!valore;
  }

  function collega(b, m, extra) {
    b.addEventListener("click", () => {
      interruttore(b, !interruttore(b));
      if (extra) extra();
      m.segna();
    });
  }

  function numero(campo, nome, minimo, massimo) {
    const t = campo.value.trim();
    if (t === "") return null;
    const n = Number(t);
    if (!Number.isInteger(n) || n < minimo || (massimo != null && n > massimo)) {
      throw new Error(nome);
    }
    return n;
  }

  // --- regole dello show
  const fr = modulo($("regole"), $("r-salva"), $("r-esito"));
  let slugRegole = null;
  let sponsorToccato = false;
  collega($("r-sp"), fr, () => { sponsorToccato = true; });

  function aggiornaRegole(d) {
    const nuovoShow = slugRegole !== d.slug;
    if (nuovoShow) { fr.pulito(); fr.dire("", false); }
    else if (!fr.puoRiempire()) return;
    slugRegole = d.slug;
    const r = d.regole || {}, g = S.impostazioni;
    const parole = (v) => (Array.isArray(v) ? v.join(", ") : v || "");
    $("r-min").value = r.min_duration_minutes != null ? r.min_duration_minutes : "";
    $("r-max").value = r.max_duration_minutes != null ? r.max_duration_minutes : "";
    $("r-min").placeholder = String(g.min_duration_minutes);
    $("r-max").placeholder = String(g.max_duration_minutes);
    $("r-in").value = parole(r.title_include);
    $("r-out").value = parole(r.title_exclude);
    interruttore($("r-sp"), r.sponsorblock != null ? r.sponsorblock : g.sponsorblock);
    sponsorToccato = false;
    fr.pulito();
  }

  $("regole").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!dettaglio) return;
    let corpo;
    try {
      corpo = {
        slug: dettaglio.slug,
        min_duration_minutes: numero($("r-min"), "durata minima", 0),
        max_duration_minutes: numero($("r-max"), "durata massima", 0),
        title_include: $("r-in").value.trim(),
        title_exclude: $("r-out").value.trim()
      };
    } catch (e) {
      fr.dire("Scrivi un numero intero di minuti, oppure lascia vuoto.", true);
      return;
    }
    if (sponsorToccato || (dettaglio.regole && dettaglio.regole.sponsorblock != null)) {
      corpo.sponsorblock = interruttore($("r-sp"));
    }
    $("r-salva").disabled = true;
    const r = await azione("/api/regole", corpo);
    if (r && r.ok) {
      fr.pulito();
      fr.dire("Regole salvate.", false);
      firme.saltatiShow = null;
      await aggiornaScheda();
    } else {
      $("r-salva").disabled = false;
      if (r) fr.dire(frase(r), true);
    }
  });

  // --- impostazioni generali
  const fi = modulo($("impostazioni"), $("s-salva"), $("s-esito"));
  collega($("g-sp"), fi);
  collega($("g-vol"), fi);
  collega($("g-cap"), fi);

  function aggiornaImpostazioni() {
    if (!fi.puoRiempire()) return;
    const i = S.impostazioni;
    $("s-ep").value = i.max_episodes;
    $("s-min").value = i.min_duration_minutes;
    $("s-max").value = i.max_duration_minutes;
    $("s-q").value = i.qualita;
    interruttore($("g-sp"), i.sponsorblock);
    interruttore($("g-vol"), i.loudnorm);
    interruttore($("g-cap"), i.chapters);
    fi.pulito();
  }

  $("impostazioni").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    let corpo;
    try {
      const ep = numero($("s-ep"), "episodi", 1, 50);
      const mn = numero($("s-min"), "minima", 0);
      const mx = numero($("s-max"), "massima", 0);
      if (ep == null || mn == null || mx == null) throw new Error("vuoto");
      corpo = {
        max_episodes: ep, min_duration_minutes: mn, max_duration_minutes: mx,
        qualita: $("s-q").value, sponsorblock: interruttore($("g-sp")),
        loudnorm: interruttore($("g-vol")), chapters: interruttore($("g-cap"))
      };
    } catch (e) {
      fi.dire("Controlla i numeri: gli episodi vanno da 1 a 50, le durate sono minuti interi.", true);
      return;
    }
    $("s-salva").disabled = true;
    const r = await azione("/api/impostazioni", corpo);
    if (r && r.ok) {
      fi.pulito();
      fi.dire("Impostazioni salvate.", false);
    } else {
      $("s-salva").disabled = false;
      if (r) fi.dire(frase(r), true);
    }
  });

  // ------------------------------------------------------------ fascia bassa
  function disegnaSpazio() {
    const sp = S.spazio;
    const pc = (x, tot) => (tot > 0 ? Math.max(0, Math.min(100, x / tot * 100)) : 0);
    const cron = sp.cronologia_mb + " di " + sp.soglia_mb + " MB";
    testo($("sp-cron"), cron);
    $("sp-cron-barra").setAttribute("aria-label", "Cronologia: " + cron);
    $("sp-cron-barra").firstElementChild.style.width = pc(sp.cronologia_mb, sp.soglia_mb) + "%";
    testo($("sp-cron-nota"), "A " + sp.soglia_mb + " MB si azzera da sola e riparte da capo.");
    testo($("sp-sito"), sp.sito_mb + " MB di " + sp.limite_sito_mb + " MB");

    if (!cambiato("spazio", [S.show.map((s) => [s.slug, s.title, s.mb]), sp.limite_sito_mb])) return;
    const barra = $("sp-sito-barra");
    barra.setAttribute("aria-label", "Spazio per show: " + S.show.map((s) => s.title + " " + s.mb + " MB").join(", "));
    barra.replaceChildren(...S.show.filter((s) => s.mb > 0).map((s) => {
      const i = el("i", { class: "c" + (S.show.indexOf(s) % 5 + 1), title: s.title + ": " + s.mb + " MB" });
      i.style.width = pc(s.mb, sp.limite_sito_mb) + "%";
      return i;
    }));
    $("sp-legenda").replaceChildren(...S.show.map((s, n) =>
      el("li", null, el("i", { class: "c" + (n % 5 + 1) }), s.title, el("b", null, s.mb + " MB"))));
  }

  function disegnaSaltatiGiro() {
    const g = S.ultimo_giro;
    const lista = g && Array.isArray(g.saltati) ? g.saltati : [];
    if (!cambiato("saltatiGiro", [lista, S.show.map((s) => [s.slug, s.title])])) return;
    testo($("salt-n"), lista.length ? String(lista.length) : "");
    const titoli = {};
    for (const s of S.show) titoli[s.slug] = s.title;
    const MAX = 8;
    $("salt-elenco").replaceChildren(...lista.slice(0, MAX).map((v) =>
      voceSaltato(v.slug, v, titoli[v.slug] || v.slug)));
    const altri = $("salt-altri");
    altri.hidden = lista.length <= MAX;
    testo(altri, "Altri " + (lista.length - MAX) + ": li trovi nella scheda dello show.");
  }

  // ------------------------------------------------------------ link e anteprima
  const pv = { n: 0, link: "", stato: "libero", dati: null, pausa: null, aggiungendo: false };

  function nota(msg, errore) {
    const p = $("aggiunta-nota");
    p.className = "hint" + (errore ? " bad" : "");
    testo(p, msg);
  }

  function chiudiAnteprima(svuota) {
    pv.n++;
    pv.stato = "libero";
    pv.dati = null;
    pv.link = "";
    clearTimeout(pv.pausa);
    $("anteprima").hidden = true;
    $("pv-max").value = "";
    $("pv-max").removeAttribute("aria-invalid");
    nota(NOTA_PREDEFINITA, false);
    if (svuota) $("link").value = "";
  }

  async function avviaAnteprima(mantieniRegola) {
    const link = mantieniRegola ? pv.link : $("link").value.trim();
    if (!link) { nota("Incolla prima il link di un canale, di una playlist o di un video.", true); return; }
    if (!mantieniRegola) $("pv-max").value = "";
    const massimo = regolaAnteprima();
    if (massimo === false) { pv.n++; $("anteprima").removeAttribute("aria-busy"); return; }  // un'anteprima in volo non deve toccare l'avviso
    const mio = ++pv.n;
    pv.link = link;
    pv.stato = "carico";
    if (!mantieniRegola) $("anteprima").hidden = true;
    $("anteprima").setAttribute("aria-busy", "true");
    nota("Guardo cosa c'è…", false);
    const corpo = { link: link };
    if (massimo != null) corpo.regole = { max_duration_minutes: massimo };
    const r = await api("/api/anteprima", { metodo: "POST", corpo: corpo });
    if (mio !== pv.n) return;
    $("anteprima").removeAttribute("aria-busy");
    if (!r) { pv.stato = "libero"; nota(NOTA_PREDEFINITA, false); return; }
    if (!r.ok || !r.dati) {
      pv.stato = "errore";
      $("anteprima").hidden = true;
      nota((r.dati && r.dati.errore) || "L'anteprima non è riuscita. Riprova.", true);
      return;
    }
    pv.stato = "pronto";
    pv.dati = r.dati;
    nota(NOTA_PREDEFINITA, false);
    mostraAnteprima(r.dati);
  }

  // null: nessuna regola; false: valore non valido (campo segnato); altrimenti i minuti.
  function regolaAnteprima() {
    const campo = $("pv-max");
    const t = campo.value.trim();
    campo.removeAttribute("aria-invalid");
    if (t === "") return null;
    const n = Number(t);
    if (Number.isInteger(n) && n > 0) return n;
    campo.setAttribute("aria-invalid", "true");
    nota("Scrivi un numero intero di minuti, oppure lascia vuoto.", true);
    return false;
  }

  function mostraAnteprima(d) {
    $("anteprima").hidden = false;
    testo($("pv-titolo"), d.title);
    const video = Array.isArray(d.video) ? d.video : [];
    const entrano = video.filter((v) => v.entra).length;
    const tipo = d.tipo === "canale" ? "Canale · ultimi " + plurale(video.length, "video", "video")
      : d.tipo === "playlist" ? "Playlist · " + plurale(video.length, "video", "video")
      : "Video singolo";
    testo($("pv-tipo"), tipo);
    $("pv-regola").hidden = d.tipo !== "canale";
    $("pv-elenco").replaceChildren(...video.map((v) => {
      const marchio = v.entra
        ? el("span", { class: "mark in" }, icona("i-check"), "Entra")
        : el("span", { class: "mark out" }, icona("i-skip"), "Saltato: " + (v.motivo || "non entra"));
      return el("li", { class: v.entra ? "" : "out" }, el("span", { class: "t" }, v.title),
        el("span", { class: "d" }, durata(v.duration)), marchio);
    }));
    const mb = "Circa " + d.stima_mb + " MB.";
    let riga;
    if (d.gia_presente) riga = "Lo segui già.";
    else if (d.tipo === "canale") riga = "Con queste regole entrano " + entrano + " video su " + video.length + ". " + mb;
    else if (d.tipo === "playlist") riga = "Nessun filtro: entrano tutti i video della playlist, fino a 50. " + mb;
    else riga = mb;
    testo($("pv-riga"), riga);
    $("pv-aggiungi").hidden = !!d.gia_presente;
    testo($("pv-aggiungi"), d.tipo === "video" ? "Aggiungi il video" : "Aggiungi lo show");
    testo($("pv-annulla"), d.gia_presente ? "Chiudi" : "Annulla");
  }

  $("aggiunta").addEventListener("submit", (ev) => { ev.preventDefault(); avviaAnteprima(false); });
  $("link").addEventListener("paste", () => setTimeout(() => avviaAnteprima(false), 0));
  $("link").addEventListener("input", () => {
    if ($("link").value.trim() !== pv.link && pv.stato !== "libero") chiudiAnteprima(false);
  });
  $("link").addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && pv.stato !== "libero") chiudiAnteprima(false);
  });
  $("pv-max").addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter") return;
    ev.preventDefault();  // il campo sta dentro il modulo del link: Invio non deve rifare l'anteprima da capo
    clearTimeout(pv.pausa);
    if (pv.link) avviaAnteprima(true);
  });
  $("pv-max").addEventListener("input", () => {
    clearTimeout(pv.pausa);
    pv.pausa = setTimeout(() => { if (pv.link) avviaAnteprima(true); }, 600);
  });
  $("pv-annulla").addEventListener("click", () => { chiudiAnteprima(true); $("link").focus(); });
  $("pv-aggiungi").addEventListener("click", async () => {
    if (pv.aggiungendo || !pv.dati || pv.dati.gia_presente) return;
    const massimo = regolaAnteprima();
    if (massimo === false) return;
    pv.aggiungendo = true;
    $("pv-aggiungi").disabled = true;
    const corpo = { link: pv.link };
    if (massimo != null && pv.dati.tipo === "canale") corpo.regole = { max_duration_minutes: massimo };
    const era = pv.dati.tipo;
    const r = await azione("/api/aggiungi", corpo,
      era === "video" ? "Scarico il video: lo trovi tra i video singoli."
        : "Aggiungo lo show: tra poco lo trovi nello scaffale.");
    pv.aggiungendo = false;
    $("pv-aggiungi").disabled = false;
    if (r && r.ok) chiudiAnteprima(true);
  });

  // ------------------------------------------------------------ avvio
  setInterval(() => { if (S) disegnaScala(); }, 60000);
  aggiornaStato();
})();
