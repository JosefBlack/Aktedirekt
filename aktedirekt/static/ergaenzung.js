"use strict";
// Zweite Stufe: Nach dem Abschicken ergänzt der Mandant auf seiner Statusseite,
// was der Fragebogen der Kanzlei sonst noch wissen will. Welche Abschnitte und
// Felder es gibt, sagt der Server; hier werden daraus Formulare gebaut.
// Benutzt $, $$, zustand, aufruf, melden, zeigen und fertig aus app.js.

let offener_abschnitt = null;

const EINGABE = {
  text: { type: "text" }, iban: { type: "text", autocapitalize: "characters", spellcheck: "false" },
  zahl: { type: "text", inputmode: "numeric" }, jahr: { type: "text", inputmode: "numeric" },
  betrag: { type: "text", inputmode: "decimal" }, telefon: { type: "tel" }, email: { type: "email" },
  datum_frei: { type: "date" }, datum: { type: "date" },
};

function knoten(art, eigenschaften, text) {
  const e = document.createElement(art);
  for (const [name, wert] of Object.entries(eigenschaften || {})) e.setAttribute(name, wert);
  if (text !== undefined) e.textContent = text;
  return e;
}

function ausgefuellt(abschnitt) {
  return Object.values(zustand.ergaenzungen[abschnitt] || {}).some((wert) => wert);
}

window.ergaenzungen_zeigen = function () {
  const block = $("#ergaenzen");
  const felder = zustand.ergaenzung_felder || [];
  block.hidden = !zustand.abgeschlossen || !felder.length;
  const liste = $("#ergaenzungsliste");
  liste.textContent = "";
  for (const a of felder) {
    const li = knoten("li", ausgefuellt(a.abschnitt) ? { class: "erledigt" } : {});
    const knopf = knoten("button", { type: "button" });
    knopf.append(knoten("span", {}, a.titel),
      knoten("span", { class: "zustand" }, ausgefuellt(a.abschnitt) ? "eingetragen ✓" : "offen ›"));
    knopf.addEventListener("click", () => abschnitt_oeffnen(a));
    li.append(knopf);
    liste.append(li);
  }
};

function abschnitt_oeffnen(a) {
  offener_abschnitt = a;
  $("#ergaenzung-titel").textContent = a.titel;
  $("#ergaenzung-hinweis").textContent = a.hinweis;
  $("#ergaenzung-hinweis").hidden = !a.hinweis;
  const ziel = $("#ergaenzung-felder");
  ziel.textContent = "";
  const werte = zustand.ergaenzungen[a.abschnitt] || {};
  for (const f of a.felder) {
    let huelle;
    if (f.art === "ja_nein") {
      huelle = knoten("fieldset");
      huelle.append(knoten("legend", {}, f.bezeichnung));
      const wahl = knoten("div", { class: "wahl" });
      for (const [wert, wort] of [["ja", "Ja"], ["nein", "Nein"]]) {
        const marke = knoten("label");
        const punkt = knoten("input", { type: "radio", name: f.name, value: wert });
        punkt.checked = werte[f.name] === wert;
        marke.append(punkt, knoten("span", {}, wort));
        wahl.append(marke);
      }
      huelle.append(wahl);
    } else {
      huelle = knoten("label", {}, f.bezeichnung + " ");
      if (f.hinweis) huelle.append(knoten("span", { class: "frei" }, f.hinweis));
      const eingabe = f.art === "lang"
        ? knoten("textarea", { name: f.name, rows: "3", maxlength: String(f.laenge) })
        : knoten("input", { name: f.name, maxlength: String(f.laenge), autocomplete: "off", ...(EINGABE[f.art] || EINGABE.text) });
      eingabe.value = werte[f.name] || "";
      huelle.append(eingabe);
    }
    if (f.zeige_wenn) huelle.dataset.zeigeWenn = f.zeige_wenn.join("=");
    ziel.append(huelle, knoten("p", { class: "fehler", "data-fehler": f.name }));
  }
  bedingte($("#ergaenzung"));
  zeigen("ergaenzung");
}

$("#ergaenzung").addEventListener("change", (e) => bedingte(e.currentTarget));

$("#ergaenzung").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  sperren(form, true);
  try {
    const daten = lesen(form);
    // Was gerade ausgeblendet ist, wird nicht mitgeschickt.
    for (const teil of $$("[data-zeige-wenn]", form)) {
      if (teil.hidden) for (const feld of $$("[name]", teil)) daten[feld.name] = "";
    }
    const antwort = await aufruf("PUT", "/api/aufnahme/ergaenzung/" + offener_abschnitt.abschnitt, daten);
    fehler_zeigen(form, antwort.fehler || {});
    if (!Object.keys(antwort.fehler || {}).length) {
      zustand.ergaenzungen = antwort.ergaenzungen;
      fertig();
    }
  } catch (fehler) { melden(fehler.message); }
  sperren(form, false);
});
