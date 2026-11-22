"use strict";
// Bausteine für die Website der Kanzlei: Knöpfe mit eigenen Farben (ohne Abhängigkeit
// von dieser Seite), Links und QR-Codes - jeweils mit der gewählten Quelle.
(function () {
  const $ = (s) => document.querySelector(s);
  const basis = location.origin + "/";
  let farbe = "#17324d";

  function adresse(ziel, quelle) {
    return basis + (ziel === "unfall" ? "?" : "?f=" + ziel + "&") + "quelle=" + encodeURIComponent(quelle);
  }

  function knopf(ziel, quelle, text, unter) {
    return '<a href="' + adresse(ziel, quelle) + '" target="_blank" rel="noopener" style="display:inline-block;'
      + 'padding:14px 22px;border-radius:10px;background:' + farbe + ';color:#fff;font:600 17px/1.3 system-ui,sans-serif;'
      + 'text-decoration:none;text-align:center">' + text + '<br><span style="font-weight:400;font-size:14px;opacity:.85">'
      + unter + '</span></a>';
  }

  function aktualisieren() {
    let quelle = $("#quelle").value.trim().toLowerCase().replace(/[^a-z0-9-]/g, "").slice(0, 30) || "website";
    const bausteine = {
      unfall: knopf("unfall", quelle, "Unfall online melden", "in etwa 5 Minuten – direkt am Handy"),
      kuendigung: knopf("kuendigung", quelle, "Kündigung erhalten?", "jetzt online prüfen lassen – Fristen laufen"),
    };
    for (const [ziel, code] of Object.entries(bausteine)) {
      $("#code-" + ziel).value = code;
      // Die Vorschau wird aus denselben Bausteinen gebaut - ohne innerHTML aus fremder Quelle.
      const vorschau = $("#vorschau-" + ziel);
      vorschau.textContent = "";
      const a = document.createElement("a");
      a.href = adresse(ziel, quelle);
      a.target = "_blank";
      a.rel = "noopener";
      a.className = "vorschau-knopf";
      a.textContent = ziel === "unfall" ? "Unfall online melden" : "Kündigung erhalten?";
      vorschau.append(a);
    }
    $("#code-links").value = "Unfall melden: " + adresse("unfall", quelle) + "\nKündigung prüfen lassen: " + adresse("kuendigung", quelle);
    for (const ziel of ["unfall", "kuendigung"]) {
      const qr = "/einbinden/qr.svg?ziel=" + ziel + "&quelle=" + encodeURIComponent(quelle);
      $("#qr-" + ziel).src = qr;
      $("#qr-" + ziel + "-link").href = qr;
    }
  }

  for (const k of document.querySelectorAll("[data-kopiere]")) {
    k.addEventListener("click", async () => {
      const feld = $("#" + k.dataset.kopiere);
      try { await navigator.clipboard.writeText(feld.value); k.textContent = "Kopiert ✓"; }
      catch (e) { feld.select(); }
    });
  }
  $("#quelle").addEventListener("input", aktualisieren);
  fetch("/marke.css").then((a) => a.text()).then((css) => {
    const treffer = css.match(/--marke:\s*(#[0-9a-fA-F]{6})/);
    if (treffer) farbe = treffer[1];
    aktualisieren();
  }).catch(aktualisieren);
  aktualisieren();
})();
