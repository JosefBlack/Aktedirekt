"use strict";
// Schadenaufnahme auf dem Handy. Der Server prüft; hier wird nur gezeigt,
// was er sagt.

const SEITEN = ["start", "unfall", "fahrzeug", "gegner", "fotos", "person", "abschluss", "fertig"];
const TITEL = { start: "Unfall melden", unfall: "Der Unfall", fahrzeug: "Ihr Fahrzeug",
  gegner: "Das andere Fahrzeug", fotos: "Fotos", person: "Ihre Daten",
  abschluss: "Unterschrift", fertig: "Eingegangen" };
const MERKER = "aktedirekt-zugang";

let zugang = "";
let zustand = null;
let seite = "start";

const $ = (s, wurzel = document) => wurzel.querySelector(s);
const $$ = (s, wurzel = document) => Array.from(wurzel.querySelectorAll(s));

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

function zeigen(name) {
  seite = name;
  melden("");
  for (const s of $$("[data-seite]")) s.hidden = s.dataset.seite !== name;
  const nr = SEITEN.indexOf(name);
  const mitten = nr > 0 && nr < SEITEN.length - 1;
  $("#titel").textContent = TITEL[name];
  $("#zurueck").hidden = !mitten;
  $("#zaehler").textContent = mitten ? `${nr} von ${SEITEN.length - 2}` : "";
  $("#fortschritt").style.width = (100 * nr / (SEITEN.length - 1)) + "%";
  if (name === "abschluss") flaeche.einrichten();
  window.scrollTo(0, 0);
}

// ---- Formulare ----------------------------------------------------------

function lesen(form) {
  const daten = {};
  for (const feld of $$("input[name], textarea[name]", form)) {
    if (feld.type === "radio") { if (feld.checked) daten[feld.name] = feld.value; else daten[feld.name] ??= ""; }
    else if (feld.type !== "checkbox") daten[feld.name] = feld.value;
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
    const gewaehlt = $(`input[name="${name}"]:checked`, form);
    teil.hidden = !(gewaehlt && gewaehlt.value === wert);
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
for (const form of $$("form")) {
  form.addEventListener("input", fehler_loeschen);
  form.addEventListener("change", fehler_loeschen);
}

function sperren(form, an) {
  const knopf = $("button.haupt", form);
  if (knopf) knopf.disabled = an;
}

for (const form of $$("form[data-schritt]")) {
  form.addEventListener("change", () => bedingte(form));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    sperren(form, true);
    try {
      const daten = lesen(form);
      const antwort = await aufruf("PUT", "/api/aufnahme/schritt/" + form.dataset.schritt, daten);
      fehler_zeigen(form, antwort.fehler || {});
      if (!Object.keys(antwort.fehler || {}).length) {
        zustand.angaben[form.dataset.schritt] = daten;
        zeigen(SEITEN[SEITEN.indexOf(form.dataset.seite) + 1]);
      }
    } catch (fehler) { melden(fehler.message); }
    sperren(form, false);
  });
}

// ---- Fotos und Unterlagen ----------------------------------------------

function dateien_zeigen() {
  for (const liste of [$("#dateiliste"), $("#dateiliste2")]) {
    liste.textContent = "";
    for (const d of zustand.dateien) {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = d.name;
      li.append(name);
      if (!zustand.abgeschlossen) {
        const weg = document.createElement("button");
        weg.type = "button"; weg.textContent = "×";
        weg.setAttribute("aria-label", d.name + " entfernen");
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
async function eine_hochladen(datei) {
  if (datei.size > 15 * 1024 * 1024) { const f = new Error("zu groß"); f.status = 413; throw f; }
  const ziel = await aufruf("POST", "/api/aufnahme/dateien", { name: datei.name, groesse: datei.size });
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

async function hochladen(auswahl, stand) {
  const dateien = Array.from(auswahl.files);
  auswahl.value = "";
  melden("");
  let nr = 0;
  for (const datei of dateien) {
    nr += 1;
    stand.hidden = false;
    stand.textContent = `Lade ${nr} von ${dateien.length} hoch …`;
    try {
      const neu = await eine_hochladen(datei);
      zustand.dateien.push(neu);
      dateien_zeigen();
    } catch (fehler) {
      melden(fehler.status === 413 ? `„${datei.name}“ ist zu groß (höchstens 15 MB).` : `„${datei.name}“: ${fehler.message}`);
    }
  }
  stand.hidden = true;
}

for (const [feld, stand] of [["kamera", "ladestand"], ["auswahl", "ladestand"], ["kamera2", "ladestand2"], ["auswahl2", "ladestand2"]]) {
  $("#" + feld).addEventListener("change", (e) => hochladen(e.target, $("#" + stand)));
}
$("#fotos-weiter").addEventListener("click", () => zeigen("person"));

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
abschluss.addEventListener("submit", async (e) => {
  e.preventDefault();
  sperren(abschluss, true);
  try {
    const antwort = await aufruf("POST", "/api/aufnahme/abschluss", {
      vollmacht: $('[name="vollmacht"]', abschluss).checked,
      datenschutz: $('[name="datenschutz"]', abschluss).checked,
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

// ---- Ablauf -------------------------------------------------------------

function fertig() {
  $("#sachstand").textContent = zustand.sachstand;
  $("#auskunft").hidden = !zustand.einwilligung_partner;
  $("#auskunft-text").textContent = zustand.partner + " darf diesen Stand sehen.";
  dateien_zeigen();
  zeigen("fertig");
}

function uebernehmen() {
  const vorbelegt = zustand.vorbelegt || {};
  for (const form of $$("form[data-schritt]")) {
    const schritt = form.dataset.schritt;
    // Was die Werkstatt eingetragen hat, steht schon da - der Mandant kann es ändern.
    fuellen(form, zustand.angaben[schritt] || vorbelegt[schritt] || {});
  }
  $("#text-vollmacht").textContent = zustand.texte.vollmacht;
  $("#text-datenschutz").textContent = zustand.texte.datenschutz;
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
      zustand = await aufruf("POST", "/api/aufnahme", { einladung });
      zugang = zustand.zugang;
      try { localStorage.setItem(MERKER, zugang); } catch (e) { /* privates Fenster */ }
      uebernehmen();
    }
    const offen = SEITEN.slice(1, -2).find((s) => s !== "fotos" && !zustand.angaben[s]);
    zeigen(offen || "abschluss");
  } catch (fehler) { melden(fehler.message); }
  $("#los").disabled = false;
});

$("#zurueck").addEventListener("click", () => zeigen(SEITEN[Math.max(0, SEITEN.indexOf(seite) - 1)]));

(async function beginnen() {
  try { zugang = localStorage.getItem(MERKER) || ""; } catch (e) { zugang = ""; }
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
      return zeigen("start");
    } catch (fehler) {
      zustand = null;
      try { zugang = localStorage.getItem(MERKER) || ""; } catch (e) { zugang = ""; }
      if (!zugang) { zeigen("start"); return melden(fehler.message); }
    }
  }
  if (zugang) {
    try {
      zustand = await aufruf("GET", "/api/aufnahme");
      uebernehmen();
      if (zustand.abgeschlossen) return fertig();
      if (Object.keys(zustand.angaben).length) $("#los").textContent = "Weitermachen";
    } catch (fehler) {
      if (fehler.status === 401) { zugang = ""; try { localStorage.removeItem(MERKER); } catch (e) { /* egal */ } }
      else melden(fehler.message);
    }
  }
  zeigen("start");
})();
