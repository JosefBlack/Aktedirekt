"use strict";
// Bildfragebogen für den Unfall (Prototyp): eine Frage je Bildschirm, Antworten
// durch Antippen. Am Ende eines Abschnitts (Unfall, Fahrzeug, Gegenseite) wird
// gespeichert - geprüft wird wie immer auf dem Server. Danach geht es auf der
// gewohnten Seite weiter mit Fotos, persönlichen Daten und Unterschrift.

const MERKER = "aktedirekt-zugang";
const ENTWURF = "aktedirekt-dialog-entwurf";
const $ = (s, w = document) => w.querySelector(s);

let zugang = "";
let antworten = {};          // Feldname -> Wert (Zeichenkette)
let verlauf = [];            // besuchte Fragen, für "Zurück"
let fehler_vom_server = {};

function knoten(art, eigenschaften, text) {
  const e = document.createElement(art);
  for (const [n, w] of Object.entries(eigenschaften || {})) e.setAttribute(n, w);
  if (text !== undefined) e.textContent = text;
  return e;
}

function melden(text) {
  $("#meldung").textContent = text || "";
  $("#meldung").hidden = !text;
}

async function aufruf(verfahren, pfad, koerper) {
  const kopf = {};
  if (zugang) kopf.Authorization = "Bearer " + zugang;
  if (koerper !== undefined) kopf["Content-Type"] = "application/json";
  let antwort;
  try {
    antwort = await fetch(pfad, { method: verfahren, headers: kopf, body: koerper === undefined ? undefined : JSON.stringify(koerper) });
  } catch (e) { throw new Error("Keine Verbindung. Bitte gleich noch einmal versuchen – Ihre Antworten bleiben erhalten."); }
  let daten = {};
  try { daten = await antwort.json(); } catch (e) { /* leer */ }
  if (antwort.status === 422) return daten;
  if (!antwort.ok) {
    const f = new Error(typeof daten.detail === "string" ? daten.detail : "Das hat nicht geklappt. Bitte noch einmal versuchen.");
    f.status = antwort.status;
    throw f;
  }
  return daten;
}

function heute(tage = 0) {
  const d = new Date(Date.now() - tage * 86400000);
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
}

// ---- Die Fragen -----------------------------------------------------------

const ARTEN = [
  { wert: "auffahren", titel: "Auffahrunfall" },
  { wert: "vorfahrt", titel: "Vorfahrt" },
  { wert: "spurwechsel", titel: "Spurwechsel" },
  { wert: "abbiegen", titel: "Abbiegen" },
  { wert: "parken", titel: "Ein- oder Ausparken" },
  { wert: "geparkt", titel: "Mein Auto stand geparkt" },
  { wert: "sonstiges", titel: "Etwas anderes" },
];
const ROLLEN = {
  auffahren: ["Wer ist aufgefahren?", [["gegner", "Der andere ist auf mich aufgefahren"], ["ich", "Ich bin aufgefahren"]]],
  vorfahrt: ["Wer hat die Vorfahrt missachtet?", [["gegner", "Der andere"], ["ich", "Ich"], ["unklar", "Das ist umstritten"]]],
  spurwechsel: ["Wer hat die Spur gewechselt?", [["gegner", "Der andere"], ["ich", "Ich"], ["beide", "Beide"]]],
  abbiegen: ["Wer ist abgebogen?", [["gegner", "Der andere"], ["ich", "Ich"], ["beide", "Beide"]]],
  parken: ["Wer hat ein- oder ausgeparkt?", [["gegner", "Der andere"], ["ich", "Ich"], ["beide", "Beide"]]],
};
const SAETZE = {
  auffahren: { gegner: "Ein anderes Fahrzeug ist auf mein Fahrzeug aufgefahren.", ich: "Ich bin auf ein anderes Fahrzeug aufgefahren." },
  vorfahrt: { gegner: "Der andere hat mir die Vorfahrt genommen.", ich: "Ich habe die Vorfahrt des anderen missachtet.", unklar: "Es kam an einer Kreuzung zum Zusammenstoß; wer Vorfahrt hatte, ist umstritten." },
  spurwechsel: { gegner: "Der andere hat die Spur gewechselt und mein Fahrzeug gestreift.", ich: "Ich habe die Spur gewechselt.", beide: "Wir haben beide gleichzeitig die Spur gewechselt." },
  abbiegen: { gegner: "Der andere ist abgebogen und mit meinem Fahrzeug zusammengestoßen.", ich: "Ich bin abgebogen.", beide: "Wir sind beide abgebogen." },
  parken: { gegner: "Der andere hat beim Ein- oder Ausparken mein Fahrzeug beschädigt.", ich: "Ich habe beim Ein- oder Ausparken ein anderes Fahrzeug beschädigt.", beide: "Wir haben beide rangiert." },
  geparkt: "Mein Fahrzeug stand ordnungsgemäß geparkt und wurde angefahren.",
};
const STELLEN = [["vorne_links", "vorne links"], ["vorne", "vorne"], ["vorne_rechts", "vorne rechts"],
  ["links", "linke Seite"], ["dach", "Dach"], ["rechts", "rechte Seite"],
  ["hinten_links", "hinten links"], ["hinten", "hinten"], ["hinten_rechts", "hinten rechts"]];

const ABSCHNITTE = { unfall: "Der Unfall", fahrzeug: "Ihr Fahrzeug", gegner: "Die Gegenseite" };
let ART = "unfall";
let hinweise = [];           // Hinweise des Servers, etwa zur Klagefrist

// Hat ein Gutachter den Schaden schon aufgenommen, braucht es keine eigenen Schadenfotos.
const ohne_gutachten = () => !["ja", "beauftragt"].includes(antworten.gutachten);

const FRAGEN = [
  // Unfall
  { id: "datum", schritt: "unfall", feld: "unfalldatum", art: "datum", frage: "Wann war der Unfall?" },
  { id: "zeit", schritt: "unfall", feld: "unfallzeit", art: "zeit", frage: "Um wie viel Uhr ungefähr?", spaeter: "Weiß ich nicht mehr" },
  { id: "ort", schritt: "unfall", feld: "unfallort", art: "text", frage: "Wo ist es passiert?", unter: "Straße und Ort genügen, zum Beispiel „Kreuzung Hauptstraße / Bahnhofstraße, Hamburg“." },
  { id: "art", schritt: "unfall", feld: "unfallart", art: "unfallart", frage: "Was ist passiert?", unter: "Tippen Sie auf das Bild, das am besten passt." },
  { id: "rolle", schritt: "unfall", feld: "verursacher", art: "rolle", wenn: () => !!ROLLEN[antworten.unfallart] },
  { id: "hergang", schritt: "unfall", feld: "hergang", art: "lang", frage: "Erzählen Sie kurz, wie es dazu kam",
    unter: "Ein paar Sätze in Ihren eigenen Worten genügen. Wir haben schon einen Anfang vorbereitet." },
  { id: "skizze", schritt: "unfall", feld: "_skizze", art: "skizze", lokal: true, frage: "Zeichnen Sie eine kleine Skizze",
    unter: "Mit dem Finger: wo die Fahrzeuge waren und wie sie gefahren sind. Grob genügt völlig.", spaeter: "Lieber später – oder auf Papier und dann fotografieren" },
  { id: "unfallfotos", schritt: "unfall", feld: "_unfallfotos", art: "fotos", lokal: true, unterlage: "unfallort", mindestens: 1,
    frage: "Laden Sie Ihre Fotos vom Unfall hoch", unter: "Sie sind besonders wichtig – sie zeigen, wie es passiert ist.",
    spaeter: "Ich habe am Unfallort keine Fotos gemacht" },
  { id: "polizei", schritt: "unfall", feld: "polizei", art: "janein", frage: "War die Polizei vor Ort?" },
  { id: "polizei_az", schritt: "unfall", feld: "polizei_az", art: "text", frage: "Haben Sie ein Aktenzeichen der Polizei?",
    unter: "Steht auf dem Zettel, den die Polizei Ihnen gegeben hat.", spaeter: "Habe ich nicht", wenn: () => antworten.polizei === "ja" },
  { id: "verletzt", schritt: "unfall", feld: "verletzt", art: "janein", frage: "Wurde jemand verletzt?" },
  { id: "zeugen_ja", schritt: "unfall", feld: "_zeugen", art: "janein", frage: "Gab es Zeugen?", lokal: true },
  { id: "zeugen", schritt: "unfall", feld: "zeugen", art: "lang", frage: "Wer hat es gesehen?", unter: "Name und, wenn bekannt, Telefon oder Anschrift.",
    spaeter: "Reiche ich später nach", wenn: () => antworten._zeugen === "ja" },
  // Fahrzeug
  { id: "kennzeichen", schritt: "fahrzeug", feld: "kennzeichen", art: "kennzeichen", frage: "Welches Kennzeichen hat Ihr Fahrzeug?" },
  { id: "gutachten", schritt: "fahrzeug", feld: "gutachten", art: "wahl", frage: "Gibt es schon ein Gutachten?",
    unter: "Dann steht alles zum Schaden – Fotos, Reparaturkosten, Wert – im Gutachten, und wir fragen danach nicht noch einmal.",
    wahl: [["ja", "Ja, das Gutachten liegt vor"], ["beauftragt", "Ein Gutachter ist beauftragt"], ["nein", "Nein, noch nicht"]], spaeter: "Weiß ich nicht" },
  { id: "stellen", schritt: "fahrzeug", feld: "schadenstellen", art: "auto", wenn: ohne_gutachten, frage: "Wo ist Ihr Fahrzeug beschädigt?", unter: "Tippen Sie auf die Stellen – auch mehrere. Dann sagen wir Ihnen, was Sie fotografieren sollen.", spaeter: "Weiß ich nicht genau" },
  { id: "schadenfotos", schritt: "fahrzeug", feld: "_schadenfotos", art: "fotos", lokal: true, unterlage: "schaden", mindestens: 1, wenn: ohne_gutachten,
    frage: "Fotografieren Sie jetzt den Schaden", spaeter: "Das Fahrzeug ist gerade nicht bei mir – ich reiche die Fotos nach" },
  { id: "halter", schritt: "fahrzeug", feld: "halter_selbst", art: "janein", frage: "Ist das Fahrzeug auf Sie zugelassen?", unter: "So steht es im Fahrzeugschein." },
  { id: "halter_name", schritt: "fahrzeug", feld: "halter_name", art: "text", frage: "Auf wen ist es zugelassen?", wenn: () => antworten.halter_selbst === "nein" },
  { id: "finanzierung", schritt: "fahrzeug", feld: "finanzierung", art: "wahl", frage: "Ist das Fahrzeug finanziert oder geleast?",
    wahl: [["nein", "Nein, es gehört mir"], ["finanziert", "Finanziert"], ["geleast", "Geleast"]] },
  { id: "vorsteuer", schritt: "fahrzeug", feld: "vorsteuer", art: "janein", frage: "Ist es ein Firmenfahrzeug mit Vorsteuerabzug?", unter: "Im Zweifel: Nein." },
  // Gegenseite
  { id: "gegner", schritt: "gegner", feld: "gegner_bekannt", art: "janein", frage: "Kennen Sie das Kennzeichen des anderen Fahrzeugs?" },
  { id: "gegner_kz", schritt: "gegner", feld: "gegner_kennzeichen", art: "kennzeichen", frage: "Wie lautet das Kennzeichen des anderen?", wenn: () => antworten.gegner_bekannt === "ja" },
  { id: "gegner_vers", schritt: "gegner", feld: "gegner_versicherung", art: "text", frage: "Wissen Sie, wo der andere versichert ist?",
    unter: "Falls Sie Daten ausgetauscht haben. Sonst finden wir es heraus.", spaeter: "Weiß ich nicht", wenn: () => antworten.gegner_bekannt === "ja" },
  { id: "gegnerfotos", schritt: "gegner", feld: "_gegnerfotos", art: "fotos", lokal: true, unterlage: "gegner",
    frage: "Haben Sie ein Foto vom Kennzeichen oder vom Unfallbericht?", unter: "Auch ausgetauschte Zettel mit Namen und Versicherung helfen.", spaeter: "Habe ich nicht" },
];

// ---- Anzeige --------------------------------------------------------------

function sichtbar() { return FRAGEN.filter((f) => !f.wenn || f.wenn()); }

function kopf_setzen(f, rueckwaerts) {
  const liste = sichtbar();
  const nr = liste.indexOf(f) + 1;
  const teile = [...new Set(liste.map((x) => ABSCHNITTE[x.schritt]))];
  const teil = teile.indexOf(ABSCHNITTE[f.schritt]) + 1;
  $("#zaehler").textContent = teil ? `Teil ${teil} von ${teile.length}` : "";
  $("#fortschritt").style.width = (100 * (nr - 1) / liste.length) + "%";
  $("#zurueck").hidden = verlauf.length < 2;
  $("#titel").textContent = ABSCHNITTE[f.schritt] || "";
}

// Variante "Gespräch": dieselben Fragen als Sprechblasen; das Beantwortete bleibt stehen.
// Grundform ist das Gespräch mit großen Bildkacheln; ?stil=karten zeigt die reine Kartenform.
const CHAT = new URLSearchParams(location.search).get("stil") !== "karten";
if (CHAT) document.body.classList.add("chat");



function frage_text(f) { return f.art === "rolle" ? ROLLEN[antworten.unfallart][0] : f.frage; }

function datum_lesbar(iso) {
  const [j, m, t] = (iso || "").split("-");
  return t ? `${t}.${m}.${j}` : iso;
}

function antwort_text(f) {
  const w = antworten[f.feld] || "";
  if (!w) return f.spaeter || "–";
  if (f.art === "datum") return w === heute() ? "Heute" : w === heute(1) ? "Gestern" : datum_lesbar(w);
  if (f.art === "zeit") return w + " Uhr";
  if (f.art === "janein") return w === "ja" ? "Ja" : "Nein";
  if (f.art === "unfallart") return (ARTEN.find((a) => a.wert === w) || {}).titel || w;
  if (f.art === "rolle") return (ROLLEN[antworten.unfallart][1].find(([x]) => x === w) || [, w])[1];
  if (f.art === "wahl") return (f.wahl.find(([x]) => x === w) || [, w])[1];
  if (f.art === "gruppe") return f.felder.map((g) => antworten[g.name]).filter(Boolean).join(", ") || "–";
  if (f.art === "text" && f.typ === "date") return datum_lesbar(w);
  if (f.art === "fotos") return w === "1" ? "1 Foto" : w + " Fotos";
  if (f.art === "skizze") return "Skizze gezeichnet ✓";
  if (f.art === "auto") return STELLEN.filter(([x]) => w.split(",").includes(x)).map(([, t]) => t).join(", ");
  return w;
}

function blase(wer, text, unter) {
  const b = knoten("div", { class: "blase " + wer });
  b.append(knoten("p", {}, text));
  if (unter) b.append(knoten("p", { class: "klein" }, unter));
  return b;
}

let tippt = null;
function chat_zeigen(f, rueckwaerts) {
  melden("");
  const buehne = $("#buehne");
  buehne.textContent = "";
  const gespraech = knoten("div", { class: "gespraech" });
  if (verlauf.length <= 1) gespraech.append(blase("bot", ART === "kuendigung"
    ? "Guten Tag! Ich stelle Ihnen ein paar Fragen zu Ihrer Kündigung. Wegen der kurzen Frist zählt jeder Tag – was Sie nicht wissen, lassen Sie einfach aus und reichen es später nach."
    : "Guten Tag! Ich stelle Ihnen ein paar kurze Fragen zu Ihrem Unfall. Was Sie nicht wissen, lassen Sie einfach aus – Sie können alles später nachreichen."));
  // Ältere Antworten eingeklappt, damit nicht endlos gescrollt werden muss.
  const bisher = verlauf.slice(0, -1).map((id) => FRAGEN.find((x) => x.id === id)).filter(Boolean);
  const sichtbar_ab = Math.max(0, bisher.length - 3);
  let ablage = null;
  if (sichtbar_ab > 0) {
    ablage = knoten("details", { class: "frueher" });
    ablage.append(knoten("summary", {}, `Ihre bisherigen Antworten (${sichtbar_ab}) – antippen zum Ansehen oder Ändern`));
    gespraech.append(ablage);
  }
  let abschnitt = bisher.length ? null : f.schritt;
  bisher.forEach((g, i) => {
    const ziel = i < sichtbar_ab ? ablage : gespraech;
    if (g.schritt !== abschnitt) {
      ziel.append(knoten("p", { class: "abschnitt" }, ABSCHNITTE[g.schritt]));
      abschnitt = g.schritt;
    }
    const antwort = blase("ich", antwort_text(g));
    antwort.title = "Antippen zum Ändern";
    antwort.addEventListener("click", () => {
      verlauf = verlauf.slice(0, verlauf.indexOf(g.id) + 1);
      chat_zeigen(g, true);
    });
    ziel.append(blase("bot alt", frage_text(g)), antwort);
  });
  if (f.schritt !== abschnitt) gespraech.append(knoten("p", { class: "abschnitt" }, ABSCHNITTE[f.schritt]));
  const eingabe = knoten("div", { class: "eingabe" });
  for (const h of hinweise) gespraech.append(blase("bot hinweis " + (h.art || ""), h.text));
  const frage = blase("bot aktuell", frage_text(f), f.unter);
  const punkte = knoten("div", { class: "blase bot tippt" });
  punkte.append(knoten("span"), knoten("span"), knoten("span"));
  buehne.append(gespraech, eingabe);
  BAU[f.art](eingabe, f);
  const fehler = fehler_vom_server[f.feld];
  if (fehler) eingabe.append(knoten("p", { class: "fehler" }, fehler));
  if (f.spaeter) {
    const spaeter = knoten("button", { type: "button", class: "spaeter" }, f.spaeter);
    spaeter.addEventListener("click", () => { if (!(f.art === "fotos" && geladen[f.unterlage])) antworten[f.feld] = ""; weiter(f); });
    eingabe.append(spaeter);
  }
  kopf_setzen(f, rueckwaerts);
  const fertig = () => {
    punkte.remove();
    gespraech.append(frage);
    eingabe.hidden = false;
    eingabe.classList.add("herein");
    // Die neue Frage oben ins Bild - unter der festen Kopfzeile, nicht ganz nach unten.
    const kopf = document.querySelector("header").offsetHeight;
    window.scrollTo(0, Math.max(0, frage.getBoundingClientRect().top + window.scrollY - kopf - 12));
  };
  clearTimeout(tippt);
  if (rueckwaerts || window.matchMedia("(prefers-reduced-motion: reduce)").matches) fertig();
  else {
    eingabe.hidden = true;
    gespraech.append(punkte);
    window.scrollTo(0, document.body.scrollHeight);
    tippt = setTimeout(fertig, 450);
  }
}

function zeigen(f, rueckwaerts = false) {
  if (CHAT) return chat_zeigen(f, rueckwaerts);
  melden("");
  const buehne = $("#buehne");
  buehne.textContent = "";
  const karte = knoten("section", { class: "frage" + (rueckwaerts ? " zurueck-an" : "") });
  const ueber = knoten("h1", {}, f.art === "rolle" ? ROLLEN[antworten.unfallart][0] : f.frage);
  karte.append(ueber);
  if (f.unter) karte.append(knoten("p", { class: "unter" }, f.unter));
  BAU[f.art](karte, f);
  const fehler = fehler_vom_server[f.feld];
  if (fehler) karte.append(knoten("p", { class: "fehler" }, fehler));
  if (f.spaeter) {
    const spaeter = knoten("button", { type: "button", class: "spaeter" }, f.spaeter);
    spaeter.addEventListener("click", () => { if (!(f.art === "fotos" && geladen[f.unterlage])) antworten[f.feld] = ""; weiter(f); });
    karte.append(spaeter);
  }
  buehne.append(karte);
  kopf_setzen(f, rueckwaerts);
  window.scrollTo(0, 0);
  const erstes = $("input, textarea", karte);
  if (erstes && !("ontouchstart" in window)) erstes.focus();
}

function knopf_weiter(karte, f, lesen, pruefen) {
  const k = knoten("button", { type: "button", class: "haupt" }, "Weiter");
  k.addEventListener("click", () => {
    const wert = lesen();
    const meldung = pruefen ? pruefen(wert) : "";
    if (meldung) { melden(meldung); return; }
    antworten[f.feld] = wert;
    weiter(f);
  });
  karte.append(k);
  return k;
}

// Drei Schritte wie auf der gewohnten Seite: ankündigen, abliefern, prüfen lassen.
const geladen = {};          // Art der Unterlage -> Anzahl hochgeladener Dateien
async function datei_hochladen(inhalt, name, unterlage) {
  if (inhalt.size > 15 * 1024 * 1024) throw new Error("„" + name + "“ ist zu groß (höchstens 15 MB).");
  const ziel = await aufruf("POST", "/api/aufnahme/dateien", { name, groesse: inhalt.size, unterlage });
  const hier = ziel.adresse.startsWith("/");
  let antwort;
  try {
    antwort = await fetch(ziel.adresse, { method: "PUT", body: inhalt, headers: hier ? { Authorization: "Bearer " + zugang } : {} });
  } catch (e) { throw new Error("Keine Verbindung. Bitte noch einmal versuchen."); }
  if (!antwort.ok) throw new Error("Das Hochladen hat nicht geklappt. Bitte noch einmal versuchen.");
  await aufruf("POST", "/api/aufnahme/dateien/" + ziel.kennung + "/fertig");
  geladen[unterlage] = (geladen[unterlage] || 0) + 1;
}

// Vorlagen für die Skizze: Straße, Kreuzung, Parkplatz - gezeichnet in 600 x 400.
const VORLAGEN = {
  strasse: (c) => { c.fillStyle = "#d5dbe1"; c.fillRect(230, 0, 140, 400); c.setLineDash([18, 16]); c.strokeStyle = "#fff"; c.lineWidth = 4;
    c.beginPath(); c.moveTo(300, 0); c.lineTo(300, 400); c.stroke(); c.setLineDash([]); },
  kreuzung: (c) => { c.fillStyle = "#d5dbe1"; c.fillRect(240, 0, 120, 400); c.fillRect(0, 140, 600, 120); c.setLineDash([16, 14]);
    c.strokeStyle = "#fff"; c.lineWidth = 4; c.beginPath(); c.moveTo(300, 0); c.lineTo(300, 130); c.moveTo(300, 270); c.lineTo(300, 400);
    c.moveTo(0, 200); c.lineTo(230, 200); c.moveTo(370, 200); c.lineTo(600, 200); c.stroke(); c.setLineDash([]); },
  parkplatz: (c) => { c.fillStyle = "#d5dbe1"; c.fillRect(0, 0, 600, 400); c.strokeStyle = "#fff"; c.lineWidth = 4; c.beginPath();
    for (const x of [100, 200, 300, 400, 500]) { c.moveTo(x, 0); c.lineTo(x, 110); c.moveTo(x, 290); c.lineTo(x, 400); }
    c.moveTo(0, 110); c.lineTo(600, 110); c.moveTo(0, 290); c.lineTo(600, 290); c.stroke(); },
};
const STIFTE = [["#2f6fb0", "Ihr Fahrzeug"], ["#e8a33d", "das andere"], ["#1d2733", "Linie"]];

const BAU = {
  fotos(karte, f) {
    if (f.unterlage === "unfallort") {
      const liste = knoten("ul", { class: "tipps" });
      for (const t of ["beide Fahrzeuge, wie sie nach dem Unfall standen", "das Kennzeichen des anderen Fahrzeugs",
        "die Schäden an beiden Fahrzeugen", "Spuren, Schilder, Ampeln und die Umgebung"]) liste.append(knoten("li", {}, t));
      karte.append(knoten("p", { class: "unter" }, "Hilfreich sind vor allem:"), liste);
    }
    if (f.unterlage === "schaden") {
      const stellen = STELLEN.filter(([w]) => (antworten.schadenstellen || "").split(",").includes(w)).map(([, t]) => t);
      const liste = knoten("ul", { class: "tipps" });
      for (const t of ["einmal das ganze Fahrzeug aus etwa drei Metern",
        ...(stellen.length ? stellen.map((x) => "die beschädigte Stelle (" + x + ") aus der Nähe") : ["jede beschädigte Stelle aus der Nähe"]),
        "das Kennzeichen, damit man das Fahrzeug erkennt"]) liste.append(knoten("li", {}, t));
      karte.append(knoten("p", { class: "unter" }, "Bitte fotografieren Sie:"), liste);
    }
    const stand = knoten("p", { class: "gewaehlt-text" });
    const zeigen_stand = () => {
      const n = geladen[f.unterlage] || 0;
      stand.textContent = n ? `✓ ${n} ${n === 1 ? "Foto" : "Fotos"} hochgeladen` : "Noch keine Fotos";
    };
    const reihe = knoten("div", { class: "kacheln" });
    for (const [text, kamera] of [["📷 Foto aufnehmen", true], ["Aus der Galerie", false]]) {
      const marke = knoten("label", { class: "kachel" });
      marke.append(knoten("span", {}, text));
      const feld = knoten("input", { type: "file", accept: kamera ? "image/*" : "image/*,application/pdf", hidden: "" });
      if (kamera) feld.setAttribute("capture", "environment"); else feld.multiple = true;
      feld.addEventListener("change", async () => {
        const dateien = Array.from(feld.files);
        feld.value = "";
        melden("");
        for (const [i, d] of dateien.entries()) {
          stand.textContent = `Lade ${i + 1} von ${dateien.length} hoch …`;
          try { await datei_hochladen(d, d.name || "foto.jpg", f.unterlage); }
          catch (e) { melden(e.message); }
        }
        zeigen_stand();
      });
      marke.append(feld);
      reihe.append(marke);
    }
    karte.append(reihe, stand);
    zeigen_stand();
    knopf_weiter(karte, f, () => String(geladen[f.unterlage] || 0),
      (w) => (f.mindestens && Number(w) < f.mindestens) ? "Bitte mindestens ein Foto – oder unten den Knopf darunter wählen." : "");
  },
  skizze(karte, f) {
    const vorlage_wahl = { vorfahrt: "kreuzung", abbiegen: "kreuzung", parken: "parkplatz" }[antworten.unfallart] || "strasse";
    let vorlage = vorlage_wahl, farbe = STIFTE[0][0];
    const striche = [];
    const leinwand = knoten("canvas", { class: "skizze", width: "600", height: "400", "aria-label": "Zeichenfläche für die Skizze" });
    const c = leinwand.getContext("2d");
    const malen = () => {
      c.fillStyle = "#fff"; c.fillRect(0, 0, 600, 400);
      VORLAGEN[vorlage](c);
      c.lineCap = "round"; c.lineJoin = "round"; c.lineWidth = 7;
      for (const s of striche) {
        c.strokeStyle = s.farbe; c.beginPath();
        s.punkte.forEach(([x, y], i) => (i ? c.lineTo(x, y) : c.moveTo(x, y)));
        if (s.punkte.length === 1) c.lineTo(s.punkte[0][0] + 0.1, s.punkte[0][1]);
        c.stroke();
      }
    };
    const ort = (e) => { const r = leinwand.getBoundingClientRect(); return [(e.clientX - r.left) * 600 / r.width, (e.clientY - r.top) * 400 / r.height]; };
    let aktiv = null;
    leinwand.addEventListener("pointerdown", (e) => { leinwand.setPointerCapture(e.pointerId); aktiv = { farbe, punkte: [ort(e)] }; striche.push(aktiv); malen(); });
    leinwand.addEventListener("pointermove", (e) => { if (aktiv) { aktiv.punkte.push(ort(e)); malen(); } });
    for (const ende of ["pointerup", "pointercancel"]) leinwand.addEventListener(ende, () => { aktiv = null; });
    const vorlagen = knoten("div", { class: "werkzeug" });
    for (const [name, text] of [["strasse", "Straße"], ["kreuzung", "Kreuzung"], ["parkplatz", "Parkplatz"]]) {
      const k = knoten("button", { type: "button", class: "chip" + (name === vorlage ? " an" : "") }, text);
      k.addEventListener("click", () => { vorlage = name; for (const x of vorlagen.children) x.classList.toggle("an", x === k); malen(); });
      vorlagen.append(k);
    }
    const stifte = knoten("div", { class: "werkzeug" });
    for (const [wert, text] of STIFTE) {
      const k = knoten("button", { type: "button", class: "chip stift" + (wert === farbe ? " an" : "") });
      k.append(knoten("i", { class: "punkt-" + STIFTE.findIndex(([w]) => w === wert) }), text);
      k.addEventListener("click", () => { farbe = wert; for (const x of stifte.children) x.classList.toggle("an", x === k); });
      stifte.append(k);
    }
    const zurueck = knoten("button", { type: "button", class: "chip" }, "↶ Rückgängig");
    zurueck.addEventListener("click", () => { striche.pop(); malen(); });
    stifte.append(zurueck);
    karte.append(vorlagen, leinwand, stifte);
    if (antworten._skizze) karte.append(knoten("p", { class: "gewaehlt-text" }, "✓ Ihre Skizze ist gespeichert. Sie können eine weitere zeichnen."));
    malen();
    const k = knoten("button", { type: "button", class: "haupt" }, "Skizze speichern und weiter");
    k.addEventListener("click", async () => {
      if (!striche.length) {
        if (antworten._skizze) return weiter(f);
        melden("Bitte zeichnen Sie etwas – oder wählen Sie unten „Lieber später“.");
        return;
      }
      k.disabled = true;
      try {
        const bild = await new Promise((fertig) => leinwand.toBlob(fertig, "image/png"));
        await datei_hochladen(bild, "Unfallskizze.png", "skizze");
        antworten._skizze = "1";
        weiter(f);
      } catch (e) { melden(e.message); }
      k.disabled = false;
    });
    karte.append(k);
  },

  datum(karte, f) {
    const schnell = knoten("div", { class: "schnell" });
    for (const [text, tage] of [["Heute", 0], ["Gestern", 1]]) {
      const k = knoten("button", { type: "button", class: "kachel" }, text);
      k.addEventListener("click", () => { antworten[f.feld] = heute(tage); weiter(f); });
      schnell.append(k);
    }
    karte.append(schnell);
    const marke = knoten("label", {}, "Oder ein anderer Tag:");
    const feld = knoten("input", { type: "date", max: heute() });
    feld.value = antworten[f.feld] || "";
    marke.append(feld);
    karte.append(marke);
    knopf_weiter(karte, f, () => feld.value, (w) => w ? "" : "Bitte den Tag wählen.");
  },
  zeit(karte, f) {
    const feld = knoten("input", { type: "time" });
    feld.value = antworten[f.feld] || "";
    karte.append(feld);
    knopf_weiter(karte, f, () => feld.value);
  },
  gruppe(karte, f) {
    const felder = [];
    for (const g of f.felder) {
      const marke = knoten("label", {}, g.bezeichnung + (g.pflicht ? "" : " "));
      if (!g.pflicht && g.hinweis) marke.append(knoten("span", { class: "frei" }, g.hinweis));
      const feld = knoten("input", { type: EINGABE[g.art] || "text", maxlength: String(g.laenge || 200), autocomplete: AUTO[g.name] || "off" });
      feld.value = antworten[g.name] || "";
      marke.append(feld);
      if (fehler_vom_server[g.name]) marke.append(knoten("span", { class: "fehler" }, fehler_vom_server[g.name]));
      karte.append(marke);
      felder.push([g, feld]);
    }
    const k = knoten("button", { type: "button", class: "haupt" }, "Weiter");
    k.addEventListener("click", () => {
      const fehlt = felder.find(([g, feld]) => g.pflicht && !feld.value.trim());
      if (fehlt) { melden("Bitte ausfüllen: " + fehlt[0].bezeichnung); fehlt[1].focus(); return; }
      for (const [g, feld] of felder) { antworten[g.name] = feld.value.trim(); delete fehler_vom_server[g.name]; }
      weiter(f);
    });
    karte.append(k);
  },
  text(karte, f) {
    const feld = knoten("input", { type: f.typ || "text", maxlength: String(f.laenge || 200), autocomplete: "off" });
    if (f.typ === "date") feld.max = heute();
    feld.value = antworten[f.feld] || "";
    karte.append(feld);
    feld.addEventListener("keydown", (e) => { if (e.key === "Enter") $("button.haupt", karte).click(); });
    knopf_weiter(karte, f, () => feld.value.trim(), (w) => (w || f.spaeter) ? "" : "Bitte ausfüllen.");
  },
  kennzeichen(karte, f) {
    const feld = knoten("input", { type: "text", maxlength: "12", autocomplete: "off", autocapitalize: "characters",
      placeholder: "z. B. HH-AB 1234", class: "kennzeichen-feld" });
    feld.value = antworten[f.feld] || "";
    karte.append(feld);
    feld.addEventListener("keydown", (e) => { if (e.key === "Enter") $("button.haupt", karte).click(); });
    knopf_weiter(karte, f, () => feld.value.trim().toUpperCase(), (w) => w ? "" : "Bitte das Kennzeichen eintragen.");
  },
  lang(karte, f) {
    const feld = knoten("textarea", { rows: "6", maxlength: f.feld === "hergang" ? "4000" : "1000" });
    if (f.feld === "hergang" && !antworten.hergang) {
      const s = SAETZE[antworten.unfallart];
      antworten.hergang = typeof s === "string" ? s : (s && s[antworten.verursacher]) || "";
      if (antworten.hergang) antworten.hergang += " ";
    }
    feld.value = antworten[f.feld] || "";
    karte.append(feld);
    knopf_weiter(karte, f, () => feld.value.trim(), (w) => (w || f.spaeter) ? "" : "Bitte ein paar Worte schreiben.");
  },
  janein(karte, f) {
    const reihe = knoten("div", { class: "kacheln" });
    for (const [wert, text] of [["ja", "Ja"], ["nein", "Nein"]]) {
      const k = knoten("button", { type: "button", class: "kachel" + (antworten[f.feld] === wert ? " gewaehlt" : "") }, text);
      k.addEventListener("click", () => { antworten[f.feld] = wert; weiter(f); });
      reihe.append(k);
    }
    karte.append(reihe);
  },
  wahl(karte, f) {
    const reihe = knoten("div", { class: "kacheln" });
    for (const [wert, text] of f.wahl) {
      const k = knoten("button", { type: "button", class: "kachel breit" + (antworten[f.feld] === wert ? " gewaehlt" : "") }, text);
      k.addEventListener("click", () => { antworten[f.feld] = wert; weiter(f); });
      reihe.append(k);
    }
    karte.append(reihe);
  },
  unfallart(karte, f) {
    const legende = knoten("p", { class: "legende" });
    for (const [klasse, text] of [["ich", "Ihr Fahrzeug"], ["andere", "das andere"]]) {
      const teil = knoten("span");
      teil.append(knoten("i", { class: klasse }), text);
      legende.append(teil);
    }
    karte.append(legende);
    const reihe = knoten("div", { class: "kacheln" });
    for (const a of ARTEN) {
      const k = knoten("button", { type: "button", class: "kachel" + (antworten.unfallart === a.wert ? " gewaehlt" : "") });
      k.append(knoten("img", { src: "bilder/" + a.wert + ".svg", alt: "" }), knoten("span", {}, a.titel));
      k.addEventListener("click", () => {
        if (antworten.unfallart !== a.wert) { antworten.verursacher = ""; if (!antworten._hergang_eigen) antworten.hergang = ""; }
        antworten.unfallart = a.wert;
        weiter(f);
      });
      reihe.append(k);
    }
    karte.append(reihe);
  },
  rolle(karte, f) {
    const reihe = knoten("div", { class: "kacheln" });
    for (const [wert, text] of ROLLEN[antworten.unfallart][1]) {
      const k = knoten("button", { type: "button", class: "kachel breit" + (antworten.verursacher === wert ? " gewaehlt" : "") }, text);
      k.addEventListener("click", () => {
        if (antworten.verursacher !== wert && !antworten._hergang_eigen) antworten.hergang = "";
        antworten.verursacher = wert;
        weiter(f);
      });
      reihe.append(k);
    }
    karte.append(reihe);
  },
  auto(karte, f) {
    const gewaehlt = new Set((antworten[f.feld] || "").split(",").filter(Boolean));
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 180 320");
    svg.setAttribute("class", "auto");
    svg.setAttribute("role", "group");
    svg.setAttribute("aria-label", "Fahrzeug von oben, vorne ist oben");
    svg.innerHTML =
      '<text x="90" y="14" text-anchor="middle" font-size="12" fill="currentColor">vorne</text>' +
      '<defs><clipPath id="karosserie"><rect x="35" y="22" width="110" height="280" rx="42"/></clipPath></defs>' +
      '<rect x="35" y="22" width="110" height="280" rx="42" fill="#dfe6ee"/>' +
      '<path d="M50 92 Q90 74 130 92 L124 118 Q90 108 56 118 Z" fill="#9fb3c8"/>' +
      '<path d="M56 224 Q90 234 124 224 L130 246 Q90 262 50 246 Z" fill="#9fb3c8"/>' +
      '<rect x="56" y="122" width="68" height="98" rx="10" fill="#c7d3df"/>' +
      '<text x="90" y="316" text-anchor="middle" font-size="12" fill="currentColor">hinten</text>';
    const zonen = document.createElementNS(ns, "g");
    zonen.setAttribute("clip-path", "url(#karosserie)");
    const spalten = [[35, 37], [72, 36], [108, 37]], zeilen = [[22, 80], [102, 120], [222, 80]];
    STELLEN.forEach(([wert, text], i) => {
      const r = document.createElementNS(ns, "rect");
      const [x, b] = spalten[i % 3], [y, h] = zeilen[Math.floor(i / 3)];
      r.setAttribute("x", x); r.setAttribute("y", y); r.setAttribute("width", b); r.setAttribute("height", h);
      r.setAttribute("rx", "6");
      r.setAttribute("class", "zone" + (gewaehlt.has(wert) ? " an" : ""));
      r.setAttribute("tabindex", "0");
      r.setAttribute("role", "checkbox");
      r.setAttribute("aria-label", text);
      r.setAttribute("aria-checked", String(gewaehlt.has(wert)));
      const umschalten = () => {
        if (gewaehlt.has(wert)) gewaehlt.delete(wert); else gewaehlt.add(wert);
        r.classList.toggle("an", gewaehlt.has(wert));
        r.setAttribute("aria-checked", String(gewaehlt.has(wert)));
        anzeigen();
      };
      r.addEventListener("click", umschalten);
      r.addEventListener("keydown", (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); umschalten(); } });
      zonen.append(r);
    });
    svg.append(zonen);
    // Der Umriss zuletzt, damit er über den markierten Stellen liegt.
    const umriss = document.createElementNS(ns, "rect");
    for (const [n, w] of Object.entries({ x: 35, y: 22, width: 110, height: 280, rx: 42, fill: "none", stroke: "#3d4a57", "stroke-width": 3 })) umriss.setAttribute(n, w);
    umriss.setAttribute("pointer-events", "none");
    svg.append(umriss);
    karte.append(svg);
    const text = knoten("p", { class: "gewaehlt-text" });
    const anzeigen = () => {
      text.textContent = STELLEN.filter(([w]) => gewaehlt.has(w)).map(([, t]) => t).join(", ") || "Noch nichts gewählt";
    };
    anzeigen();
    karte.append(text);
    knopf_weiter(karte, f, () => STELLEN.map(([w]) => w).filter((w) => gewaehlt.has(w)).join(","));
  },
};

// ---- Kündigung: Fragen aus dem Fragebogen der Kanzlei ----------------------

const EINGABE = { telefon: "tel", email: "email", plz: "text", zahl: "text", betrag: "text", datum_frei: "date", datum_kommend: "date" };
const AUTO = { vorname: "given-name", nachname: "family-name", strasse: "street-address", plz: "postal-code",
  ort: "address-level2", telefon: "tel", email: "email" };
const WORTE = { ja: "Ja", nein: "Nein", unbekannt: "Weiß ich nicht", schriftlich: "Schriftlich", muendlich: "Mündlich",
  beides: "Beides", nicht_relevant: "Trifft nicht zu" };
// Diese Felder stehen zusammen auf einem Bildschirm - eine Frage je Feld wäre hier zu kleinteilig.
const GRUPPEN = { kperson: ["vorname", "nachname", "strasse", "plz", "ort", "telefon", "email", "geburtsdatum"],
  arbeitgeber: ["arbeitgeber_name", "arbeitgeber_anschrift", "arbeitgeber_telefon", "arbeitsort"] };
const GRUPPEN_FRAGE = { kperson: "Wie erreichen wir Sie?", arbeitgeber: "Wer ist Ihr Arbeitgeber?" };

function kuendigung_fragen(abschnitte) {
  const neu = [];
  for (const a of abschnitte) {
    ABSCHNITTE[a.abschnitt] = a.titel;
    const gruppe = GRUPPEN[a.abschnitt] || [];
    if (gruppe.length) {
      neu.push({ id: a.abschnitt + "-gruppe", schritt: a.abschnitt, feld: "_" + a.abschnitt, art: "gruppe",
        frage: GRUPPEN_FRAGE[a.abschnitt] || a.titel, felder: a.felder.filter((g) => gruppe.includes(g.name)) });
    }
    for (const g of a.felder) {
      if (gruppe.includes(g.name)) continue;
      const f = { id: g.name, schritt: a.abschnitt, feld: g.name, frage: g.bezeichnung, unter: g.hinweis || "" };
      if (g.zeige_wenn) f.wenn = () => antworten[g.zeige_wenn[0]] === g.zeige_wenn[1];
      if (g.art === "ja_nein") f.art = "janein";
      else if (g.art === "auswahl") { f.art = "wahl"; f.wahl = g.auswahl.map((w) => [w, WORTE[w] || w]); }
      else if (g.art === "datum") f.art = "datum";
      else if (g.art === "lang") { f.art = "lang"; f.laenge = g.laenge; }
      else { f.art = "text"; f.typ = EINGABE[g.art] || "text"; f.laenge = g.laenge; }
      if (!g.pflicht && f.art !== "janein" && f.art !== "wahl") f.spaeter = g.art.startsWith("datum") ? "Weiß ich nicht" : "Überspringen";
      if (!g.pflicht && (f.art === "janein" || f.art === "wahl") && a.abschnitt === "besonderes") f.spaeter = "Möchte ich nicht sagen";
      neu.push(f);
    }
    if (a.abschnitt === "vorpruefung") {
      ABSCHNITTE._schreiben = "Das Kündigungsschreiben";
      neu.push({ id: "kschreiben", schritt: "_schreiben", feld: "_kschreiben", art: "fotos", lokal: true, unterlage: "kuendigung",
        frage: "Fotografieren Sie das Kündigungsschreiben", unter: "Alle Seiten – und den Briefumschlag, falls Sie ihn noch haben. Daraus lesen wir die Daten und Fristen ab.",
        spaeter: "Habe ich gerade nicht zur Hand" });
    }
  }
  FRAGEN.splice(0, FRAGEN.length, ...neu);
}

// ---- Ablauf ---------------------------------------------------------------

function entwurf_merken() {
  try { localStorage.setItem(ENTWURF + ":" + zugang.slice(0, 12), JSON.stringify(antworten)); } catch (e) { /* egal */ }
}

let fortsetzen = "";         // nach einer Korrektur: zurück zu dieser Frage

async function weiter(f) {
  if (f.feld === "hergang") antworten._hergang_eigen = "1";
  delete fehler_vom_server[f.feld];
  for (const g of f.felder || []) delete fehler_vom_server[g.name];
  entwurf_merken();
  const liste = sichtbar();
  let naechste = liste[liste.indexOf(f) + 1];
  // War das eine Korrektur, die der Server verlangt hat? Dann gleich speichern und
  // dorthin zurück, wo der Mandant war - nicht alle Fragen dazwischen noch einmal.
  if (fortsetzen && !Object.keys(fehler_vom_server).length) {
    const ok = await speichern(f.schritt);
    if (!ok) return;
    const ziel = liste.find((x) => x.id === fortsetzen);
    fortsetzen = "";
    if (ziel && ziel.schritt !== f.schritt) naechste = ziel;
    else if (ziel) naechste = ziel;
    if (!naechste) return geschafft();
    verlauf.push(naechste.id);
    return zeigen(naechste);
  }
  // Abschnitt zu Ende: speichern, bevor es weitergeht.
  if (!naechste || naechste.schritt !== f.schritt) {
    const ok = await speichern(f.schritt);
    if (!ok) return;
  }
  if (!naechste) return geschafft();
  verlauf.push(naechste.id);
  zeigen(naechste);
}

async function speichern(schritt) {
  const daten = {};
  for (const f of FRAGEN) {
    if (f.schritt !== schritt || f.lokal) continue;
    const an = !f.wenn || f.wenn();
    if (f.art === "gruppe") for (const g of f.felder) daten[g.name] = an ? (antworten[g.name] || "") : "";
    else daten[f.feld] = an ? (antworten[f.feld] || "") : "";
  }
  if (!Object.keys(daten).length) return true;        // etwa nur Fotos: nichts zu speichern
  try {
    const antwort = await aufruf("PUT", "/api/aufnahme/schritt/" + schritt, daten);
    const fehler = antwort.fehler || {};
    if (antwort.hinweise) hinweise = antwort.hinweise;
    if (Object.keys(fehler).length) {
      fehler_vom_server = fehler;
      const erste = sichtbar().find((f) => fehler[f.feld] || (f.felder || []).some((g) => fehler[g.name]));
      if (erste) {
        // Merken, wohin es nach der Korrektur weitergeht: die Frage nach diesem Abschnitt.
        const liste = sichtbar();
        const danach = liste.slice(liste.findIndex((x) => x.schritt === schritt)).find((x) => x.schritt !== schritt);
        fortsetzen = danach ? danach.id : "";
        verlauf.push(erste.id);
        zeigen(erste, true);
        melden("Bitte prüfen Sie diese Angabe – danach geht es dort weiter, wo Sie waren.");
      }
      else melden(Object.values(fehler)[0]);
      return false;
    }
    return true;
  } catch (e) { melden(e.message); return false; }
}

function geschafft() {
  $("#fortschritt").style.width = "100%";
  $("#zaehler").textContent = "";
  $("#zurueck").hidden = true;
  const buehne = $("#buehne");
  if (CHAT) {
    const gespraech = $(".gespraech", buehne);
    const letzte = FRAGEN.find((x) => x.id === verlauf[verlauf.length - 1]);
    if (gespraech && letzte) gespraech.append(blase("ich", antwort_text(letzte)),
      blase("bot", ART === "kuendigung"
        ? "Vielen Dank – fast geschafft! Jetzt nur noch die Vollmacht und Ihre Unterschrift. Arbeitsvertrag und Lohnabrechnungen laden Sie hoch, sobald wir Ihren Fall geprüft haben."
        : "Vielen Dank – der schwierigste Teil ist geschafft! Jetzt noch Ihre Kontaktdaten und die Unterschrift. Weitere Unterlagen wie Fahrzeugschein oder Rechnungen laden Sie hoch, sobald wir Ihren Fall geprüft haben."));
    const eingabe = $(".eingabe", buehne);
    eingabe.textContent = "";
    const k = knoten("button", { type: "button", class: "haupt" }, ART === "kuendigung" ? "Weiter zur Unterschrift" : "Weiter zu Kontaktdaten und Unterschrift");
    k.addEventListener("click", () => { location.href = "/#weiter"; });
    eingabe.append(k);
    window.scrollTo(0, document.body.scrollHeight);
    return;
  }
  buehne.textContent = "";
  const karte = knoten("section", { class: "frage geschafft" });
  karte.append(knoten("div", { class: "haken-gross" }, "✓"),
    knoten("h1", {}, "Geschafft – der schwierigste Teil ist erledigt"),
    knoten("p", { class: "unter" }, "Jetzt noch Ihre Kontaktdaten und die Unterschrift. Weitere Unterlagen laden Sie hoch, sobald wir Ihren Fall geprüft haben."));
  const k = knoten("button", { type: "button", class: "haupt" }, ART === "kuendigung" ? "Weiter zur Unterschrift" : "Weiter zu Kontaktdaten und Unterschrift");
  k.addEventListener("click", () => { location.href = "/#weiter"; });
  karte.append(k);
  buehne.append(karte);
}

$("#zurueck").addEventListener("click", () => {
  if (verlauf.length < 2) return;
  verlauf.pop();
  const f = FRAGEN.find((x) => x.id === verlauf[verlauf.length - 1]);
  if (f) zeigen(f, true);
});

(async function beginnen() {
  try { zugang = localStorage.getItem(MERKER) || ""; } catch (e) { zugang = ""; }
  let zustand = null;
  try {
    if (zugang) {
      try { zustand = await aufruf("GET", "/api/aufnahme"); }
      catch (e) { if (e.status === 401) zugang = ""; else throw e; }
    }
    if (!zustand) {
      const einladung = new URLSearchParams(location.search).get("p") || "";
      const quelle = new URLSearchParams(location.search).get("quelle") || "";
      const art = new URLSearchParams(location.search).get("f") === "kuendigung" ? "kuendigung" : "unfall";
      zustand = await aufruf("POST", "/api/aufnahme", { einladung, art, quelle });
      zugang = zustand.zugang;
      try { localStorage.setItem(MERKER, zugang); } catch (e) { /* privates Fenster */ }
    }
  } catch (e) { melden(e.message); return; }
  const verlangt = new URLSearchParams(location.search).get("f");
  if (verlangt && verlangt !== zustand.art) { location.href = "/?f=" + encodeURIComponent(verlangt); return; }
  if (zustand.abgeschlossen || !["unfall", "kuendigung"].includes(zustand.art)) { location.href = "/"; return; }
  ART = zustand.art;
  hinweise = zustand.hinweise || [];
  if (ART === "kuendigung") {
    kuendigung_fragen(zustand.schritt_felder || []);
    document.title = "Kündigung prüfen lassen";
  }
  // Was schon da ist (auch vom Tresen der Werkstatt), steht als Antwort bereit.
  for (const quelle of [zustand.vorbelegt || {}, zustand.angaben || {}]) {
    for (const werte of Object.values(quelle)) Object.assign(antworten, werte);
  }
  if (antworten.zeugen) antworten._zeugen = "ja";
  for (const u of zustand.unterlagen || []) geladen[u.schluessel] = u.anzahl;
  if (geladen.skizze) antworten._skizze = "1";
  if (antworten.hergang) antworten._hergang_eigen = "1";
  try { Object.assign(antworten, JSON.parse(localStorage.getItem(ENTWURF + ":" + zugang.slice(0, 12)) || "{}")); } catch (e) { /* egal */ }
  verlauf = [FRAGEN[0].id];
  zeigen(FRAGEN[0]);
})();
