"use strict";
// Fallliste für Werkstatt und Sachverständigen. Die Sitzung lebt nur in
// diesem Fenster: Wer es schließt, ist abgemeldet.

const MERKER = "aktedirekt-partner";
const $ = (s) => document.querySelector(s);
let sitzung = "";
let faelle = [];

function melden(text) {
  $("#meldung").textContent = text || "";
  $("#meldung").hidden = !text;
}

async function aufruf(verfahren, pfad, koerper) {
  const kopf = {};
  if (sitzung) kopf.Authorization = "Bearer " + sitzung;
  if (koerper) kopf["Content-Type"] = "application/json";
  let antwort;
  try {
    antwort = await fetch(pfad, { method: verfahren, headers: kopf, body: koerper ? JSON.stringify(koerper) : undefined });
  } catch (e) {
    throw new Error("Keine Verbindung. Bitte später noch einmal versuchen.");
  }
  let daten = {};
  try { daten = await antwort.json(); } catch (e) { /* 204 */ }
  if (!antwort.ok) {
    const fehler = new Error(typeof daten.detail === "string" ? daten.detail : "Das hat nicht geklappt.");
    fehler.status = antwort.status;
    throw fehler;
  }
  return daten;
}

function datum(iso) {
  const [j, m, t] = iso.split("-");
  return t + "." + m + "." + j;
}

function farbe(stand) {
  if (/reguliert$/i.test(stand) && /vollst/i.test(stand)) return "gut";
  if (/abgeschlossen|nicht übernommen/i.test(stand)) return "ende";
  return "laeuft";
}

function teil(art, klasse, text) {
  const e = document.createElement(art);
  if (klasse) e.className = klasse;
  if (text !== undefined) e.textContent = text;
  return e;
}

function zeigen() {
  const frage = $("#suche").value.trim().toLowerCase().replace(/[\s-]/g, "");
  const liste = $("#faelle");
  liste.textContent = "";
  const treffer = faelle.filter((f) => !frage ||
    (f.mandant + f.kennzeichen).toLowerCase().replace(/[\s-]/g, "").includes(frage));
  for (const f of treffer) {
    const li = teil("li");
    li.append(teil("span", "wer", f.mandant));
    const stand = teil("div", "stand");
    stand.append(teil("span", "marke " + farbe(f.sachstand), f.sachstand));
    if (f.sachstand_seit) stand.append(teil("span", "seit", "seit " + datum(f.sachstand_seit)));
    li.append(stand);
    li.append(teil("span", "kfz", f.kennzeichen + " · Unfall am " + datum(f.unfalldatum)));
    if (f.fehlende_unterlagen.length) li.append(teil("div", "fehlt", "Es fehlt noch: " + f.fehlende_unterlagen.join(", ")));
    liste.append(li);
  }
  $("#leer").hidden = faelle.length > 0;
  $("#anzahl").textContent = faelle.length
    ? (treffer.length === faelle.length ? faelle.length + (faelle.length === 1 ? " Fall" : " Fälle")
       : treffer.length + " von " + faelle.length + " Fällen")
    : "";
}

async function laden() {
  const daten = await aufruf("GET", "/api/partner/faelle");
  // Ohne Sachstand gibt es nichts zu zeigen.
  faelle = daten.faelle.filter((f) => f.sachstand);
  $("#name").textContent = daten.name;
  $("#kundenlink").value = location.origin + "/?p=" + encodeURIComponent(daten.einladung);
  $("#anmeldung").hidden = true;
  $("#liste").hidden = false;
  $("#abmelden").hidden = false;
  zeigen();
}

function abgemeldet(text) {
  sitzung = "";
  try { sessionStorage.removeItem(MERKER); } catch (e) { /* egal */ }
  faelle = [];
  $("#faelle").textContent = "";
  $("#liste").hidden = true;
  $("#abmelden").hidden = true;
  $("#anmeldung").hidden = false;
  melden(text || "");
}

$("#anmeldung").addEventListener("submit", async (e) => {
  e.preventDefault();
  melden("");
  const form = e.currentTarget;
  form.querySelector("button").disabled = true;
  try {
    const daten = await aufruf("POST", "/api/partner/anmeldung",
      { kennung: form.kennung.value, kennwort: form.kennwort.value });
    sitzung = daten.sitzung;
    try { sessionStorage.setItem(MERKER, sitzung); } catch (e2) { /* egal */ }
    form.kennwort.value = "";
    await laden();
  } catch (fehler) { melden(fehler.message); }
  form.querySelector("button").disabled = false;
});

$("#abmelden").addEventListener("click", async () => {
  try { await aufruf("POST", "/api/partner/abmeldung"); } catch (e) { /* die Sitzung läuft ohnehin ab */ }
  abgemeldet();
});

$("#suche").addEventListener("input", zeigen);

$("#kopieren").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("#kundenlink").value); $("#kopieren").textContent = "Kopiert"; }
  catch (e) { $("#kundenlink").select(); }
});

(async function beginnen() {
  try { sitzung = sessionStorage.getItem(MERKER) || ""; } catch (e) { sitzung = ""; }
  if (!sitzung) return abgemeldet();
  try { await laden(); }
  catch (fehler) { abgemeldet(fehler.status === 401 ? "Bitte erneut anmelden." : fehler.message); }
})();
