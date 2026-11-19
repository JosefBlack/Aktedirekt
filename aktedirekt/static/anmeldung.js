"use strict";
// Anmeldung für Partner und Kanzlei, mit dem zweiten Faktor, wo er verlangt
// ist. Ein Aufruf je Klick: Er gibt die Sitzung zurück - oder nichts, wenn der
// Server erst noch den Code (oder das Einrichten) verlangt.

async function anmeldung_schritt(form, aufruf) {
  const daten = await aufruf("POST", "/api/partner/anmeldung",
    { kennung: form.kennung.value, kennwort: form.kennwort.value, code: form.code.value });
  if (daten.sitzung) return daten;
  const faktor = form.querySelector("#faktor");
  const einrichten = form.querySelector("#faktor-einrichten");
  faktor.hidden = false;
  einrichten.hidden = !daten.einrichten;
  if (daten.einrichten) {
    form.querySelector("#faktor-qr").src = daten.einrichten.qr;
    form.querySelector("#faktor-geheimnis").textContent = daten.einrichten.geheimnis.replace(/(.{4})/g, "$1 ").trim();
  }
  form.code.value = "";
  form.code.focus();
  return null;
}

function anmeldung_zuruecksetzen(form) {
  form.kennwort.value = "";
  form.code.value = "";
  form.querySelector("#faktor").hidden = true;
  form.querySelector("#faktor-einrichten").hidden = true;
  // Den QR-Code nicht im Fenster stehen lassen.
  form.querySelector("#faktor-qr").removeAttribute("src");
  form.querySelector("#faktor-geheimnis").textContent = "";
}
