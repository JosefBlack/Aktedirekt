"use strict";
// Aufnahme auf dem Handy: Verkehrsunfall und Kündigung. Der Server prüft; hier
// wird nur gezeigt, was er sagt. Die Seiten des Unfalls stehen fest in
// index.html; die der Kündigung baut dieses Programm aus den Angaben des Servers.

const UNFALL_SEITEN = ["start", "unfall", "fahrzeug", "gegner", "fotos", "person", "abschluss", "fertig"];
let SEITEN = UNFALL_SEITEN;
const TITEL = { start: "Aktedirekt", unfall: "Der Unfall", fahrzeug: "Ihr Fahrzeug",
  gegner: "Das andere Fahrzeug", fotos: "Unterlagen", person: "Ihre Daten",
  abschluss: "Unterschrift", fertig: "Ihr Fall" };
const START = {
  unfall: { titel: "Unfall melden", dauer: "In etwa fünf Minuten nehmen wir Ihren Unfall auf.",
    bereit: ["Kennzeichen des anderen Fahrzeugs", "Fotos vom Schaden und vom Unfallort", "Ihren Fahrzeugschein"] },
  kuendigung: { titel: "Kündigung erhalten", dauer: "In etwa zehn Minuten nehmen wir Ihre Angaben auf.",
    bereit: ["das Kündigungsschreiben", "Ihren Arbeitsvertrag", "die letzten Lohnabrechnungen"] },
};
const WORTE = { ja: "Ja", nein: "Nein", unbekannt: "Weiß nicht", schriftlich: "Schriftlich",
  muendlich: "Mündlich", beides: "Beides", nicht_relevant: "Trifft nicht zu" };
const MERKER = "aktedirekt-zugang";
// ?formular=1: die bisherigen Formularseiten statt des Bildfragebogens (zum Vergleich und als Ausweg).
const NUR_FORMULAR = new URLSearchParams(location.search).get("formular") === "1";

let zugang = "";
let zustand = null;
let seite = "start";
let gewaehlt = "";

const $ = (s, wurzel = document) => wurzel.querySelector(s);
const $$ = (s, wurzel = document) => Array.from(wurzel.querySelectorAll(s));

function knoten(art, eigenschaften, text) {
  const e = document.createElement(art);
  for (const [name, wert] of Object.entries(eigenschaften || {})) e.setAttribute(name, wert);
  if (text !== undefined) e.textContent = text;
  return e;
}

function melden(text) {
  const m = $("#meldung");
  m.textContent = text || "";
  m.hidden = !text;
  if (text) window.scrollTo(0, 0);
}

async function aufruf(verfahren, pfad, koerper, roh) {
  const kopf = {};
  if (zugang) kopf.Authorization = "Bearer " + zugang;
  let inhalt = koerper;
  if (koerper !== undefined && !roh) {
    kopf["Content-Type"] = "application/json";
    inhalt = JSON.stringify(koerper);
  }
  let antwort;
  try {
    antwort = await fetch(pfad, { method: verfahren, headers: kopf, body: inhalt });
  } catch (e) {
    throw new Error("Keine Verbindung. Bitte später noch einmal versuchen – Ihre bisherigen Angaben sind gespeichert.");
  }
  let daten = {};
  try { daten = await antwort.json(); } catch (e) { /* 204 oder kein JSON */ }
  if (antwort.status === 422) return daten;
  if (!antwort.ok) {
    const fehler = new Error(typeof daten.detail === "string" ? daten.detail : "Das hat nicht geklappt. Bitte noch einmal versuchen.");
    fehler.status = antwort.status;
    throw fehler;
  }
  return daten;
}

// Was über den Angaben stehen soll - bei einer Kündigung vor allem die Frist.
function hinweise_zeigen() {
  const ziel = $("#hinweise");
  ziel.textContent = "";
  if (!zustand || seite === "start" || seite === "ergaenzung") return;
  for (const h of zustand.hinweise || []) ziel.append(knoten("p", { class: "hinweis " + h.art }, h.text));
}

function zeigen(name) {
  seite = name;
  melden("");
  for (const s of $$("[data-seite]")) s.hidden = s.dataset.seite !== name;
  // Die Ergänzungen der zweiten Stufe hängen an der Statusseite, nicht am Ablauf.
  const nebenan = name === "ergaenzung";
  const nr = nebenan ? SEITEN.length - 1 : SEITEN.indexOf(name);
  const mitten = nr > 0 && nr < SEITEN.length - 1;
  $("#titel").textContent = nebenan ? "Ergänzen" : TITEL[name];
  $("#zurueck").hidden = !(mitten || nebenan);
  $("#zaehler").textContent = mitten ? `${nr} von ${SEITEN.length - 2}` : "";
  $("#fortschritt").style.width = (100 * nr / (SEITEN.length - 1)) + "%";
  hinweise_zeigen();
  if (name === "abschluss") flaeche.einrichten();
  window.scrollTo(0, 0);
}

function naechste() {
  zeigen(SEITEN[SEITEN.indexOf(seite) + 1]);
}

// ---- Formulare ----------------------------------------------------------

function lesen(form) {
  const daten = {};
  for (const feld of $$("input[name], textarea[name]", form)) {
    if (feld.type === "radio") { if (feld.checked) daten[feld.name] = feld.value; else daten[feld.name] ??= ""; }
    else if (feld.type !== "checkbox") daten[feld.name] = feld.value;
  }
  // Was gerade ausgeblendet ist, wird nicht mitgeschickt.
  for (const teil of $$("[data-zeige-wenn]", form)) {
    if (teil.hidden) for (const feld of $$("[name]", teil)) daten[feld.name] = "";
  }
  return daten;
}

function fuellen(form, daten) {
  for (const feld of $$("input[name], textarea[name]", form)) {
    const wert = daten[feld.name];
    if (wert === undefined) continue;
    if (feld.type === "radio") feld.checked = feld.value === wert;
    else if (feld.type !== "checkbox") feld.value = wert;
  }
  bedingte(form);
}

function bedingte(form) {
  for (const teil of $$("[data-zeige-wenn]", form)) {
    const [name, wert] = teil.dataset.zeigeWenn.split("=");
    const gewaehltes = $(`input[name="${name}"]:checked`, form);
    teil.hidden = !(gewaehltes && gewaehltes.value === wert);
  }
}

function fehler_zeigen(form, fehler) {
  let erstes = null;
  for (const p of $$("[data-fehler]", form)) {
    p.textContent = fehler[p.dataset.fehler] || "";
    if (p.textContent && !erstes) erstes = p;
  }
  if (erstes) erstes.scrollIntoView({ block: "center", behavior: "smooth" });
}

// Eine Meldung verschwindet, sobald der Mandant die Angabe anfasst.
function fehler_loeschen(e) {
  if (!e.target.name) return;
  const p = $('[data-fehler="' + e.target.name + '"]', e.currentTarget);
  if (p) p.textContent = "";
}

function sperren(form, an) {
  const knopf = $("button.haupt", form);
  if (knopf) knopf.disabled = an;
}

const EINGABE = {
  text: { type: "text" }, iban: { type: "text", autocapitalize: "characters", spellcheck: "false" },
  zahl: { type: "text", inputmode: "numeric" }, jahr: { type: "text", inputmode: "numeric" },
  plz: { type: "text", inputmode: "numeric" }, betrag: { type: "text", inputmode: "decimal" },
  telefon: { type: "tel" }, email: { type: "email" }, datum_frei: { type: "date" }, datum_kommend: { type: "date" }, datum: { type: "date" },
  zeit: { type: "time" }, kennzeichen: { type: "text", autocapitalize: "characters" },
};

// Baut die Eingabefelder eines Abschnitts aus den Angaben des Servers.
function felder_bauen(ziel, felder, werte) {
  ziel.textContent = "";
  for (const f of felder) {
    let huelle;
    if (f.art === "ja_nein" || f.art === "auswahl") {
      const wahl = f.art === "ja_nein" ? ["ja", "nein"] : f.auswahl;
      huelle = knoten("fieldset");
      huelle.append(knoten("legend", {}, f.bezeichnung));
      if (f.hinweis) huelle.append(knoten("p", { class: "klein" }, f.hinweis));
      const reihe = knoten("div", { class: wahl.length === 3 ? "wahl drei" : "wahl" });
      for (const wert of wahl) {
        const marke = knoten("label");
        const punkt = knoten("input", { type: "radio", name: f.name, value: wert });
        punkt.checked = werte[f.name] === wert;
        marke.append(punkt, knoten("span", {}, WORTE[wert] || wert));
        reihe.append(marke);
      }
      huelle.append(reihe);
    } else {
      huelle = knoten("label", {}, f.bezeichnung + " ");
      if (f.hinweis) huelle.append(knoten("span", { class: "frei" }, f.hinweis));
      const eingabe = f.art === "lang"
        ? knoten("textarea", { name: f.name, rows: "4", maxlength: String(f.laenge) })
        : knoten("input", { name: f.name, maxlength: String(f.laenge), autocomplete: "off", ...(EINGABE[f.art] || EINGABE.text) });
      eingabe.value = werte[f.name] || "";
      huelle.append(eingabe);
    }
    if (f.zeige_wenn) huelle.dataset.zeigeWenn = f.zeige_wenn.join("=");
    ziel.append(huelle, knoten("p", { class: "fehler", "data-fehler": f.name }));
  }
}

// Ein Schritt der ersten Stufe: speichern und weiter.
function schritt_anbinden(form) {
  form.addEventListener("input", fehler_loeschen);
  form.addEventListener("change", (e) => { fehler_loeschen(e); bedingte(form); });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    sperren(form, true);
    try {
      const daten = lesen(form);
      const antwort = await aufruf("PUT", "/api/aufnahme/schritt/" + form.dataset.schritt, daten);
      fehler_zeigen(form, antwort.fehler || {});
      if (!Object.keys(antwort.fehler || {}).length) {
        zustand.angaben[form.dataset.schritt] = antwort.angaben || daten;
        zustand.hinweise = antwort.hinweise || [];
        naechste();
      }
    } catch (fehler) { melden(fehler.message); }
    sperren(form, false);
  });
}
for (const form of $$("form[data-schritt]")) schritt_anbinden(form);
for (const form of $$("form:not([data-schritt])")) {
  form.addEventListener("input", fehler_loeschen);
  form.addEventListener("change", fehler_loeschen);
}

// Richtet Ablauf und Seiten nach der Fallart der Aufnahme ein.
function ablauf_einrichten() {
  const ziel = $("#dynamisch");
  ziel.textContent = "";
  if (!zustand.schritt_felder.length) { SEITEN = UNFALL_SEITEN; return; }
  SEITEN = ["start", ...zustand.ablauf, "fotos", "abschluss", "fertig"];
  for (const a of zustand.schritt_felder) {
    TITEL[a.abschnitt] = a.titel;
    const form = knoten("form", { "data-seite": a.abschnitt, "data-schritt": a.abschnitt, novalidate: "", hidden: "" });
    form.append(knoten("h1", {}, a.titel));
    if (a.hinweis) form.append(knoten("p", { class: "klein" }, a.hinweis));
    const felder = knoten("div");
    felder_bauen(felder, a.felder, zustand.angaben[a.abschnitt] || {});
    form.append(felder, knoten("button", { type: "submit", class: "haupt" }, "Weiter"));
    ziel.append(form);
    schritt_anbinden(form);
    bedingte(form);
  }
}

// ---- Fotos und Unterlagen ----------------------------------------------

function dateien_zeigen() {
  const zahl = zustand.dateien.length;
  for (const z of [$("#dateizahl"), $("#dateizahl2")]) {
    z.textContent = zahl ? `Hochgeladen: ${zahl} ${zahl === 1 ? "Datei" : "Dateien"}` : "Noch nichts hochgeladen";
  }
  checkliste_zeigen();
  for (const liste of [$("#dateiliste"), $("#dateiliste2")]) {
    liste.textContent = "";
    for (const d of zustand.dateien) {
      const li = knoten("li");
      li.append(knoten("span", {}, d.name + (d.von ? " · von " + d.von : "")));
      // Was die Werkstatt eingereicht hat, nimmt der Mandant nicht heraus.
      if (!zustand.abgeschlossen && !d.von) {
        const weg = knoten("button", { type: "button", "aria-label": d.name + " entfernen" }, "×");
        weg.addEventListener("click", async () => {
          try {
            await aufruf("DELETE", "/api/aufnahme/dateien/" + d.kennung);
            zustand.dateien = zustand.dateien.filter((x) => x.kennung !== d.kennung);
            dateien_zeigen();
          } catch (fehler) { melden(fehler.message); }
        });
        li.append(weg);
      }
      liste.append(li);
    }
  }
}

// Drei Schritte: ankündigen, abliefern, prüfen lassen. In AWS geht das Foto
// dabei unmittelbar in den Dateispeicher der Kanzlei und nicht durch die Anwendung.
async function eine_hochladen(datei, unterlage) {
  if (datei.size > 15 * 1024 * 1024) { const f = new Error("zu groß"); f.status = 413; throw f; }
  const ziel = await aufruf("POST", "/api/aufnahme/dateien", { name: datei.name, groesse: datei.size, unterlage: unterlage || "" });
  const hier = ziel.adresse.startsWith("/");
  let antwort;
  try {
    antwort = await fetch(ziel.adresse, { method: "PUT", body: datei,
      headers: hier ? { Authorization: "Bearer " + zugang } : {} });
  } catch (e) {
    throw new Error("Keine Verbindung. Bitte noch einmal versuchen.");
  }
  if (!antwort.ok) throw new Error("Das Hochladen hat nicht geklappt. Bitte noch einmal versuchen.");
  return aufruf("POST", "/api/aufnahme/dateien/" + ziel.kennung + "/fertig");
}

async function hochladen(auswahl, stand, unterlage) {
  const dateien = Array.from(auswahl.files);
  auswahl.value = "";
  melden("");
  let nr = 0;
  for (const datei of dateien) {
    nr += 1;
    stand.hidden = false;
    stand.textContent = `Lade ${nr} von ${dateien.length} hoch …`;
    try {
      const neu = await eine_hochladen(datei, unterlage);
      zustand.dateien.push(neu);
      const punkt = (zustand.unterlagen || []).find((u) => u.schluessel === unterlage);
      if (punkt) punkt.anzahl += 1;
      dateien_zeigen();
    } catch (fehler) {
      melden(fehler.status === 413 ? `„${datei.name}“ ist zu groß (höchstens 15 MB).` : `„${datei.name}“: ${fehler.message}`);
    }
  }
  stand.hidden = true;
}

// Die Checkliste: je Art von Unterlage ein Punkt mit eigenem Knopf. So weiß
// die Kanzlei, was eine Datei ist, und der Mandant sieht, was noch fehlt.
const ANNEHMEN = "image/jpeg,image/png,image/heic,image/heif,application/pdf";

function knopf_datei(text, kamera, unterlage, stand) {
  const marke = knoten("label", { class: "knopf klein-knopf" }, text);
  const feld = knoten("input", { type: "file", accept: kamera ? "image/*" : ANNEHMEN, hidden: "" });
  if (kamera) feld.setAttribute("capture", "environment"); else feld.multiple = true;
  feld.addEventListener("change", () => hochladen(feld, stand, unterlage));
  marke.append(feld);
  return marke;
}

// Offen ist, was die Kanzlei angefordert hat und seitdem nicht kam - oder, ohne
// Anforderung, was sie sicher braucht und noch fehlt.
function ist_offen(u) {
  return u.angefordert ? !u.nachgereicht : (u.wichtig && !u.anzahl);
}

function checkliste_zeigen() {
  for (const [ziel, stand] of [[$("#checkliste"), $("#ladestand")], [$("#checkliste2"), $("#ladestand2")]]) {
    ziel.textContent = "";
    // Nach dem Abschicken zuerst, was noch fehlt; der Rest liegt eine Ebene tiefer.
    const kurz = ziel.id === "checkliste2";
    let weitere = null;
    // Vor der Prüfung durch die Kanzlei nur das Nötigste; der Rest kommt nach der Freigabe.
    let erlaubt = (zustand.unterlagen || []).filter((u) => zustand.freigegeben || u.vorab);
    // Nach dem Abschicken steht oben, was noch fehlt.
    if (ziel.id === "checkliste2") erlaubt = [...erlaubt.filter(ist_offen), ...erlaubt.filter((u) => !ist_offen(u))];
    const spaeter = (zustand.unterlagen || []).filter((u) => !zustand.freigegeben && !u.vorab);
    for (const u of erlaubt) {
      if (kurz && !ist_offen(u) && !weitere) {
        weitere = knoten("details", { class: "weitere" });
        weitere.append(knoten("summary", {}, "Weitere Unterlagen nachreichen"), knoten("ul", { class: "checkliste" }));
        const huelle = knoten("li", { class: "huelle" });
        huelle.append(weitere);
        ziel.append(huelle);
      }
      const li = knoten("li", { class: ist_offen(u) ? "offen" : (u.anzahl ? "da" : "") });
      const kopf = knoten("div", { class: "punkt" });
      const marke = u.angefordert ? (u.nachgereicht ? "✓ nachgereicht" : "angefordert")
        : (u.anzahl ? `✓ ${u.anzahl}` : (u.wichtig ? "wichtig" : ""));
      kopf.append(knoten("strong", {}, u.titel), knoten("span", { class: "marke" }, marke));
      li.append(kopf);
      if (u.hinweis) li.append(knoten("p", { class: "klein" }, u.hinweis));
      const knoepfe = knoten("div", { class: "knoepfe" });
      knoepfe.append(knopf_datei("Foto", true, u.schluessel, stand), knopf_datei("Datei", false, u.schluessel, stand));
      li.append(knoepfe);
      (weitere && !ist_offen(u) ? $("ul", weitere) : ziel).append(li);
    }
    if (spaeter.length) {
      const li = knoten("li", { class: "huelle" });
      li.append(knoten("p", { class: "klein gesperrt" }, "Sobald wir Ihren Fall geprüft haben – meist innerhalb eines Werktags –, "
        + "können Sie hier außerdem hochladen: " + spaeter.map((u) => u.titel).join(", ") + "."));
      ziel.append(li);
    }
  }
  const anf = zustand.anforderung;
  $("#anforderung").hidden = !anf;
  if (anf) {
    const punkte = $("#anforderung-punkte");
    punkte.textContent = "";
    for (const u of (zustand.unterlagen || []).filter((x) => x.angefordert)) {
      punkte.append(knoten("li", { class: u.nachgereicht ? "erledigt" : "" }, u.titel + (u.nachgereicht ? " – nachgereicht ✓" : "")));
    }
    $("#anforderung-text").textContent = anf.text || "";
    $("#anforderung-text").hidden = !anf.text;
  }
  const offen = (zustand.unterlagen || []).filter((u) => (zustand.freigegeben || u.vorab) && ist_offen(u)).map((u) => u.titel);
  $("#offen").hidden = !offen.length || !!anf;
  $("#offen").textContent = offen.length ? "Noch offen: " + offen.join(", ") + "." : "";
}
$("#fotos-weiter").addEventListener("click", naechste);

// ---- Unterschrift -------------------------------------------------------

const flaeche = {
  leinwand: $("#unterschrift"), stift: null, striche: 0, zeichnet: false,
  einrichten() {
    const l = this.leinwand, dichte = window.devicePixelRatio || 1;
    const breite = l.clientWidth, hoehe = l.clientHeight;
    if (!breite || l.width === Math.round(breite * dichte)) return;
    l.width = Math.round(breite * dichte); l.height = Math.round(hoehe * dichte);
    this.stift = l.getContext("2d");
    this.stift.scale(dichte, dichte);
    this.leeren();
  },
  leeren() {
    if (!this.stift) return;
    this.stift.fillStyle = "#fff";
    this.stift.fillRect(0, 0, this.leinwand.clientWidth, this.leinwand.clientHeight);
    this.stift.strokeStyle = "#10203a"; this.stift.lineWidth = 2.4;
    this.stift.lineCap = "round"; this.stift.lineJoin = "round";
    this.striche = 0;
  },
  ort(e) { const r = this.leinwand.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; },
};
flaeche.leinwand.addEventListener("pointerdown", (e) => {
  $('[data-fehler="unterschrift"]').textContent = "";
  flaeche.einrichten();
  flaeche.zeichnet = true;
  flaeche.leinwand.setPointerCapture(e.pointerId);
  const [x, y] = flaeche.ort(e);
  flaeche.stift.beginPath(); flaeche.stift.moveTo(x, y);
});
flaeche.leinwand.addEventListener("pointermove", (e) => {
  if (!flaeche.zeichnet) return;
  const [x, y] = flaeche.ort(e);
  flaeche.stift.lineTo(x, y); flaeche.stift.stroke();
  flaeche.striche += 1;
});
for (const ende of ["pointerup", "pointercancel"]) {
  flaeche.leinwand.addEventListener(ende, () => { flaeche.zeichnet = false; });
}
$("#loeschen").addEventListener("click", () => flaeche.leeren());

const abschluss = $('form[data-seite="abschluss"]');
let nachgefragt = false;
abschluss.addEventListener("submit", async (e) => {
  e.preventDefault();
  const offen = (zustand.erklaerungen || []).find((x) => x.wichtig && !$('[name="' + x.name + '"]', abschluss).checked);
  if (offen && !nachgefragt) {
    const frage = $("#block-" + offen.name + " .nachfrage");
    if (frage) { frage.hidden = false; frage.scrollIntoView({ block: "center", behavior: "smooth" }); return; }
  }
  sperren(abschluss, true);
  try {
    const antwort = await aufruf("POST", "/api/aufnahme/abschluss", {
      ...Object.fromEntries((zustand.erklaerungen || []).map((e) => [e.name, $('[name="' + e.name + '"]', abschluss).checked])),
      einwilligung_partner: $('[name="einwilligung_partner"]', abschluss).checked,
      // Ein paar Punkte sind keine Unterschrift.
      unterschrift: flaeche.striche >= 12 ? flaeche.leinwand.toDataURL("image/png") : "",
    });
    const fehler = antwort.fehler || {};
    fehler_zeigen(abschluss, fehler);
    if (!Object.keys(fehler).length) { zustand = { ...zustand, ...antwort }; fertig(); }
    else if (antwort.fehlend && antwort.fehlend.length) zeigen(antwort.fehlend[0]);
  } catch (fehler) { melden(fehler.message); }
  sperren(abschluss, false);
});

$("#widerrufen").addEventListener("click", async () => {
  try {
    zustand = { ...zustand, ...(await aufruf("DELETE", "/api/aufnahme/einwilligung")) };
    fertig();
  } catch (fehler) { melden(fehler.message); }
});

// ---- Erklärungen und Unterlagen ----------------------------------------

// Vollmacht, Widerrufsrecht, Datenverarbeitung: Text, Kästchen und der Text als PDF.
function erklaerungen_bauen() {
  const ziel = $("#erklaerungen");
  ziel.textContent = "";
  for (const e of zustand.erklaerungen || []) {
    const huelle = e.wichtig ? knoten("div", { class: "wichtig-block", id: "block-" + e.name }) : null;
    if (huelle) { ziel.append(huelle); }
    const z = huelle || ziel;
    if (e.titel) z.append(knoten("h2", {}, e.titel));
    if (e.text) {
      z.append(knoten("div", { class: "text " + (e.wichtig ? "" : "lang"), tabindex: "0" }, e.text));
      const pdf = knoten("button", { type: "button", class: "verweis" }, e.titel + " als PDF speichern");
      pdf.addEventListener("click", () => unterlage_speichern(e).catch((fehler) => melden(fehler.message)));
      z.append(pdf);
    }
    const marke = knoten("label", { class: "haken" + (e.wichtig ? " gross" : "") });
    marke.append(knoten("input", { type: "checkbox", name: e.name }), knoten("span", {}, e.haken + (e.pflicht ? "" : " ")));
    if (!e.pflicht && !e.wichtig) marke.lastChild.append(knoten("span", { class: "frei" }, "freiwillig"));
    z.append(marke, knoten("p", { class: "fehler", "data-fehler": e.name }));
    if (e.wichtig) {
      // Fehlt der Haken, fragt die Seite vor dem Abschicken einmal nach.
      const frage = knoten("div", { class: "nachfrage", hidden: "" });
      frage.append(knoten("p", {}, "Ohne diese Zustimmung dürfen wir erst nach Ablauf der Widerrufsfrist von 14 Tagen für Sie tätig werden – Fristen und Ansprüche können dann gefährdet sein."));
      const ja = knoten("button", { type: "button", class: "haupt" }, "Zustimmen und abschicken");
      const nein = knoten("button", { type: "button", class: "neben" }, "Ohne Zustimmung abschicken");
      ja.addEventListener("click", () => { $('[name="' + e.name + '"]', abschluss).checked = true; nachgefragt = true; abschluss.requestSubmit(); });
      nein.addEventListener("click", () => { nachgefragt = true; abschluss.requestSubmit(); });
      frage.append(ja, nein);
      z.append(frage);
    }
  }
}

async function unterlage_speichern(e) {
  let antwort;
  try {
    antwort = await fetch("/api/aufnahme/unterlagen/" + e.name, { headers: { Authorization: "Bearer " + zugang } });
  } catch (f) { throw new Error("Keine Verbindung. Bitte noch einmal versuchen."); }
  if (!antwort.ok) throw new Error("Das hat nicht geklappt. Bitte noch einmal versuchen.");
  const verweis = knoten("a", { href: URL.createObjectURL(await antwort.blob()), download: e.titel + ".pdf" });
  verweis.click();
  setTimeout(() => URL.revokeObjectURL(verweis.href), 60000);
}

function unterlagen_zeigen() {
  const mit_text = (zustand.erklaerungen || []).filter((e) => e.text);
  $("#unterlagen").hidden = !zustand.abgeschlossen || !mit_text.length;
  const liste = $("#unterlagenliste");
  liste.textContent = "";
  for (const e of mit_text) {
    const knopf = knoten("button", { type: "button" });
    knopf.append(knoten("span", {}, e.titel), knoten("span", { class: "zustand" }, "PDF ›"));
    knopf.addEventListener("click", () => unterlage_speichern(e).catch((fehler) => melden(fehler.message)));
    const li = knoten("li");
    li.append(knopf);
    liste.append(li);
  }
}

// ---- Nachricht und persönlicher Link -------------------------------------

const nachricht = $("#nachricht");
nachricht.addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#nachricht-ok").hidden = true;
  sperren(nachricht, true);
  try {
    const antwort = await aufruf("POST", "/api/aufnahme/nachricht", { text: $("#nachricht-text").value });
    fehler_zeigen(nachricht, antwort.fehler || {});
    if (!Object.keys(antwort.fehler || {}).length) {
      zustand = { ...zustand, ...antwort };
      $("#nachricht-text").value = "";
      $("#nachricht-ok").hidden = false;
      nachrichten_zeigen();
    }
  } catch (fehler) { melden(fehler.message); }
  sperren(nachricht, false);
});

// Der Zugang steht hinter dem # und reist deshalb nicht zum Server.
$("#link-sichern").addEventListener("click", async () => {
  const link = location.origin + "/#z=" + encodeURIComponent(zugang);
  try {
    if (navigator.share) await navigator.share({ title: "Mein Fall – Aktedirekt", url: link });
    else await navigator.clipboard.writeText(link);
    $("#link-ok").hidden = !!navigator.share;
  } catch (e) {
    if (e && e.name === "AbortError") return;
    window.prompt("Ihr persönlicher Link – bitte kopieren und gut aufbewahren:", link);
  }
});

// ---- Ablauf -------------------------------------------------------------

function datum_lesbar(iso) {
  if (!iso) return "";
  const [j, m, t] = iso.slice(0, 10).split("-");
  return `${t}.${m}.${j}` + (iso.length > 10 ? ", " + iso.slice(11, 16) + " Uhr" : "");
}

function fragen_zeigen() {
  const ziel = $("#fragen");
  ziel.textContent = "";
  for (const f of zustand.fragen || []) {
    const d = knoten("details");
    d.append(knoten("summary", {}, f.frage), knoten("p", {}, f.antwort));
    ziel.append(d);
  }
  $("#fragen-block").hidden = !(zustand.fragen || []).length;
}

function nachrichten_zeigen() {
  const ziel = $("#nachrichten");
  ziel.textContent = "";
  for (const n of zustand.nachrichten || []) ziel.append(knoten("li", {}, "Nachricht gesendet am " + datum_lesbar(n.am)));
}

function fertig() {
  als_app_hinweis();
  const neu = !zustand.sachstand || zustand.sachstand === "Aufgenommen";
  $("#fertig-titel").textContent = neu ? "Vielen Dank" : "Ihr Fall";
  $("#fertig-dank").hidden = !neu;
  $("#sachstand-seit").textContent = zustand.sachstand_seit ? " seit " + datum_lesbar(zustand.sachstand_seit) : "";
  $("#stand-was").textContent = (zustand.erklaerung || {}).was || "";
  $("#stand-tun").textContent = (zustand.erklaerung || {}).tun || "";
  fragen_zeigen();
  nachrichten_zeigen();
  const verlauf = $("#verlauf");
  verlauf.textContent = "";
  for (const v of zustand.verlauf || []) {
    const li = knoten("li", { class: (v.erreicht ? "erreicht" : "") + (v.aktuell ? " aktuell" : "") }, v.titel);
    if (v.aktuell) li.setAttribute("aria-current", "step");
    verlauf.append(li);
  }
  $("#anrufen").hidden = !zustand.telefon;
  $("#anrufen").textContent = "";
  if (zustand.telefon) {
    $("#anrufen").append("Lieber persönlich? ", knoten("a", { href: "tel:" + zustand.telefon.replace(/[^0-9+]/g, "") }, zustand.telefon));
  }
  $("#sachstand").textContent = zustand.sachstand;
  $("#auskunft").hidden = !zustand.einwilligung_partner;
  $("#auskunft-text").textContent = zustand.partner + " darf diesen Stand sehen.";
  dateien_zeigen();
  unterlagen_zeigen();
  if (window.ergaenzungen_zeigen) window.ergaenzungen_zeigen();
  zeigen("fertig");
}

// Texte der Startseite und die Hinweise zum Nachreichen kommen vom Server
// (Fassung der Kanzlei); bis sie da sind, gelten die hier hinterlegten.
let START_TEXTE = null;
const START_GELADEN = fetch("/api/start").then((a) => a.ok ? a.json() : null).then((d) => {
  if (!d) return;
  START_TEXTE = d.start || null;
  for (const p of $$("[data-nachreichen]")) p.textContent = (d.nachreichen || {})[p.dataset.nachreichen] || "";
  if (d.anderes_anliegen) $("#anderes").textContent = d.anderes_anliegen;
  if (gewaehlt) start_zeigen(gewaehlt);
}).catch(() => {});

function start_zeigen(art) {
  const s = (START_TEXTE && START_TEXTE[art]) || START[art];
  $("#wahl").hidden = !!art;
  $("#start-inhalt").hidden = !art;
  if (!s) return;
  $("#start-titel").textContent = s.titel;
  const versprechen = $("#start-versprechen");
  versprechen.textContent = "";
  for (const punkt of s.versprechen || [s.dauer]) versprechen.append(knoten("li", {}, punkt));
  const liste = $("#start-bereit");
  liste.textContent = "";
  for (const punkt of s.bereit) liste.append(knoten("li", {}, punkt));
}

function uebernehmen() {
  gewaehlt = zustand.art;
  ablauf_einrichten();
  start_zeigen(zustand.art);
  const vorbelegt = zustand.vorbelegt || {};
  for (const form of $$("form[data-schritt]")) {
    const schritt = form.dataset.schritt;
    // Was die Werkstatt eingetragen hat, steht schon da - der Mandant kann es ändern.
    fuellen(form, zustand.angaben[schritt] || vorbelegt[schritt] || {});
  }
  erklaerungen_bauen();
  $("#text-partner").textContent = zustand.texte.einwilligung_partner;
  $("#block-partner").hidden = !zustand.partner;
  $("#partnerhinweis").hidden = !zustand.partner;
  $("#partnerhinweis").textContent = zustand.partner ? "Aufnahme über " + zustand.partner : "";
  dateien_zeigen();
}

$("#los").addEventListener("click", async () => {
  $("#los").disabled = true;
  try {
    if (!zustand) {
      const einladung = new URLSearchParams(location.search).get("p") || "";
      const quelle = new URLSearchParams(location.search).get("quelle") || "";
      zustand = await aufruf("POST", "/api/aufnahme", { einladung, art: gewaehlt || "unfall", quelle });
      zugang = zustand.zugang;
      try { localStorage.setItem(MERKER, zugang); } catch (e) { /* privates Fenster */ }
      uebernehmen();
    }
    const offen = SEITEN.slice(1, -2).find((s) => s !== "fotos" && !zustand.angaben[s]);
    // Der Unfall beginnt im Bildfragebogen; sind Unfall, Fahrzeug und Gegenseite schon da, geht es hier weiter.
    const im_dialog = zustand.art === "unfall" ? ["unfall", "fahrzeug", "gegner"] : (zustand.ablauf || []);
    if (im_dialog.includes(offen) && !NUR_FORMULAR) {
      location.href = "/dialog";
      return;
    }
    zeigen(offen || "abschluss");
  } catch (fehler) { melden(fehler.message); }
  $("#los").disabled = false;
});

$("#zurueck").addEventListener("click", () => {
  if (seite === "ergaenzung") return fertig();
  zeigen(SEITEN[Math.max(0, SEITEN.indexOf(seite) - 1)]);
});

// Ohne Aufnahme und ohne Vorgabe in der Adresse fragt die Startseite, worum es geht.
async function wahl_zeigen() {
  const frage = new URLSearchParams(location.search);
  gewaehlt = frage.get("p") ? "unfall" : (START[frage.get("f")] ? frage.get("f") : "");
  start_zeigen(gewaehlt);
  if (gewaehlt) return;
  try {
    const daten = await aufruf("GET", "/api/start");
    $("#anderes").textContent = daten.anderes_anliegen;
  } catch (e) { /* die Auswahl geht auch ohne diesen Satz */ }
}
for (const knopf of $$("[data-fallart]")) {
  knopf.addEventListener("click", () => { gewaehlt = knopf.dataset.fallart; start_zeigen(gewaehlt); window.scrollTo(0, 0); });
}

// ---- Als App auf dem Startbildschirm --------------------------------------

const ALS_APP = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
let einbauen = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  einbauen = e;
  $("#installieren").hidden = false;
  $("#anleitung-android").hidden = true;
});
$("#installieren").addEventListener("click", async () => {
  if (!einbauen) return;
  einbauen.prompt();
  try { await einbauen.userChoice; } catch (e) { /* egal */ }
  einbauen = null;
  $("#installieren").hidden = true;
});
function als_app_hinweis() {
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
  $("#als-app").hidden = ALS_APP;
  $("#anleitung-ios").hidden = !ios;
  $("#anleitung-android").hidden = ios || !!einbauen;
}

// Im App-Symbol ohne gespeicherten Zugang (auf dem iPhone hat das Symbol einen
// eigenen Speicher): Link einfügen statt von vorn beginnen.
function link_uebernehmen(text) {
  const wert = (text || "").trim();
  const stelle = wert.indexOf("#");
  const teile = new URLSearchParams(stelle >= 0 ? wert.slice(stelle + 1) : "");
  if (teile.get("z") || teile.get("u")) {
    location.hash = stelle >= 0 ? wert.slice(stelle + 1) : "";
    location.reload();
    return true;
  }
  return false;
}
$("#link-oeffnen").addEventListener("click", () => {
  $("#link-fehler").textContent = link_uebernehmen($("#link-eingabe").value) ? "" : "Das ist kein Link zu Ihrem Fall. Bitte den ganzen Link einfügen.";
});
$("#neu-melden").addEventListener("click", (e) => {
  e.preventDefault();
  $("#ohne-zugang").hidden = true;
  wahl_zeigen();
});

// ---- Fall wiederfinden per E-Mail -----------------------------------------

const wieder = $("#wiederfinden");
async function wiederfinden_zeigen() {
  for (const id of ["wahl", "start-inhalt", "ohne-zugang"]) $("#" + id).hidden = true;
  wieder.hidden = false;
  $("#wiederfinden-ok").hidden = true;
  try {
    const d = await aufruf("GET", "/api/wiederfinden");
    $("button.haupt", wieder).hidden = !d.per_email;
    $("#wiederfinden-feld").hidden = !d.per_email;
    $("#wiederfinden-tel").hidden = d.per_email;
    $("#wiederfinden-tel").textContent = d.per_email ? "" : "Bitte rufen Sie uns an" + (d.telefon ? " unter " + d.telefon : "") + " – wir schicken Ihnen einen neuen Link.";
  } catch (e) { /* dann eben mit Formular */ }
  window.scrollTo(0, 0);
}
for (const k of $$("[data-wiederfinden]")) k.addEventListener("click", wiederfinden_zeigen);
$("#wiederfinden-zurueck").addEventListener("click", () => {
  wieder.hidden = true;
  if (ALS_APP && !zustand) $("#ohne-zugang").hidden = false; else wahl_zeigen();
});
wieder.addEventListener("submit", async (e) => {
  e.preventDefault();
  sperren(wieder, true);
  try {
    const antwort = await aufruf("POST", "/api/wiederfinden", { email: $('[name="email"]', wieder).value });
    fehler_zeigen(wieder, antwort.fehler || {});
    $("#wiederfinden-ok").hidden = !antwort.gesendet;
  } catch (fehler) { melden(fehler.message); }
  sperren(wieder, false);
});

(async function beginnen() {
  try { zugang = localStorage.getItem(MERKER) || ""; } catch (e) { zugang = ""; }
  // Persönlicher Link von einem anderen Gerät: Zugang übernehmen.
  const mitgebracht = new URLSearchParams(location.hash.slice(1)).get("z");
  if (mitgebracht) {
    history.replaceState(null, "", location.pathname);
    zugang = mitgebracht;
    try { localStorage.setItem(MERKER, zugang); } catch (e) { /* privates Fenster */ }
  }
  // Code vom Tresen: hinter dem # der Adresse, damit er nicht zum Server reist.
  const code = new URLSearchParams(location.hash.slice(1)).get("u");
  if (code) {
    history.replaceState(null, "", location.pathname);
    try {
      zugang = "";
      zustand = await aufruf("POST", "/api/aufnahme/uebernahme", { code });
      zugang = zustand.zugang;
      try { localStorage.setItem(MERKER, zugang); } catch (e) { /* privates Fenster */ }
      uebernehmen();
      if (zustand.abgeschlossen) return fertig();
      return zeigen("start");
    } catch (fehler) {
      zustand = null;
      try { zugang = localStorage.getItem(MERKER) || ""; } catch (e) { zugang = ""; }
      if (!zugang) { await wahl_zeigen(); zeigen("start"); return melden(fehler.message); }
    }
  }
  if (zugang) {
    try {
      zustand = await aufruf("GET", "/api/aufnahme");
      // Verlangt der Link ausdrücklich eine andere Fallart (?f=kuendigung), beginnt ein neuer
      // Fall. Der bisherige Zugang bleibt auf dem Gerät aufbewahrt, nur nicht mehr vorne.
      const verlangt = new URLSearchParams(location.search).get("f");
      if (verlangt && START[verlangt] && zustand.art !== verlangt && location.hash !== "#weiter") {
        try {
          const frueher = JSON.parse(localStorage.getItem(MERKER + "-frueher") || "[]");
          localStorage.setItem(MERKER + "-frueher", JSON.stringify([...frueher, zugang].slice(-10)));
          localStorage.removeItem(MERKER);
        } catch (e) { /* egal */ }
        zugang = "";
        zustand = null;
        throw Object.assign(new Error(""), { neu: true });
      }
      uebernehmen();
      if (zustand.abgeschlossen) return fertig();
      if (Object.keys(zustand.angaben).length) $("#los").textContent = "Weitermachen";
      // Vom Bildfragebogen: gleich beim nächsten offenen Schritt weiter.
      if (location.hash === "#weiter") {
        history.replaceState(null, "", location.pathname);
        const offen = SEITEN.slice(1, -2).find((s) => s !== "fotos" && !zustand.angaben[s]);
        return zeigen(offen || "abschluss");
      }
    } catch (fehler) {
      if (fehler.neu) { /* neuer Fall der verlangten Art */ }
      else if (fehler.status === 401) { zugang = ""; try { localStorage.removeItem(MERKER); } catch (e) { /* egal */ } }
      else melden(fehler.message);
    }
  }
  if (!zustand && ALS_APP) {
    $("#ohne-zugang").hidden = false;
    $("#wahl").hidden = true;
    $("#start-inhalt").hidden = true;
    return zeigen("start");
  }
  if (!zustand) await wahl_zeigen();
  zeigen("start");
})();
