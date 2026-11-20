"use strict";
// Zweite Stufe: Nach dem Abschicken ergänzt der Mandant auf seiner Statusseite,
// was der Fragebogen der Kanzlei sonst noch wissen will. Welche Abschnitte und
// Felder es gibt, sagt der Server; hier werden daraus Formulare gebaut.
// Benutzt $, $$, knoten, felder_bauen, zustand, aufruf, melden, zeigen und fertig aus app.js.

let offener_abschnitt = null;

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
    const fertig_ = ausgefuellt(a.abschnitt);
    const li = knoten("li", fertig_ ? { class: "erledigt" } : (a.wichtig ? { class: "wichtig" } : {}));
    const knopf = knoten("button", { type: "button" });
    knopf.append(knoten("span", {}, a.titel),
      knoten("span", { class: "zustand" }, fertig_ ? "eingetragen ✓" : (a.wichtig ? "bitte eintragen ›" : "offen ›")));
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
  felder_bauen($("#ergaenzung-felder"), a.felder, zustand.ergaenzungen[a.abschnitt] || {});
  bedingte($("#ergaenzung"));
  zeigen("ergaenzung");
}

$("#ergaenzung").addEventListener("change", (e) => bedingte(e.currentTarget));

$("#ergaenzung").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  sperren(form, true);
  try {
    const antwort = await aufruf("PUT", "/api/aufnahme/ergaenzung/" + offener_abschnitt.abschnitt, lesen(form));
    fehler_zeigen(form, antwort.fehler || {});
    if (!Object.keys(antwort.fehler || {}).length) {
      zustand.ergaenzungen = antwort.ergaenzungen;
      fertig();
    }
  } catch (fehler) { melden(fehler.message); }
  sperren(form, false);
});
