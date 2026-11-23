"use strict";
// Das Erscheinungsbild der Kanzlei: Name oder Logo in der Kopfzeile, Impressum
// und Datenschutz unten. Woher: /marke.json (eingestellt beim Bau des Pakets).
(async function marke() {
  let m;
  try {
    const antwort = await fetch("/marke.json");
    if (!antwort.ok) return;
    m = await antwort.json();
  } catch (e) { return; }
  const leiste = document.getElementById("markenleiste");
  if (leiste && (m.logo || m.name)) {
    leiste.textContent = "";
    if (m.logo) {
      const bild = document.createElement("img");
      bild.src = "/marke/logo";
      bild.alt = m.name || "Logo";
      bild.className = "logo";
      leiste.append(bild);
    }
    const text = document.createElement("span");
    text.className = "kanzlei";
    const name = document.createElement("strong");
    name.textContent = m.kurzname || m.name || "";
    text.append(name);
    if (m.unterzeile) {
      const unter = document.createElement("span");
      unter.textContent = m.unterzeile;
      text.append(unter);
    }
    if (name.textContent || m.unterzeile) leiste.append(text);
    leiste.hidden = false;
  }
  if (m.name) document.title = m.name + " – " + document.title;
  // Ein Zierbild der Kanzlei (etwa die Hamburger Skyline) auf der Startseite.
  const schmuck = document.getElementById("schmuck");
  if (schmuck && m.schmuck) {
    const bild = document.createElement("img");
    bild.src = "/marke/schmuck";
    bild.alt = "";
    schmuck.append(bild);
    schmuck.hidden = false;
  }
  const fuss = document.getElementById("fuss");
  if (fuss) {
    fuss.textContent = "";
    const kopf = document.createElement("p");
    kopf.className = "fuss-name";
    kopf.textContent = m.kurzname || m.name || "";
    fuss.append(kopf);
    if (m.adresse || m.telefon) {
      const kontakt = document.createElement("p");
      if (m.adresse) kontakt.append(m.adresse);
      if (m.telefon) {
        if (m.adresse) kontakt.append(" · ");
        const tel = document.createElement("a");
        tel.href = "tel:" + m.telefon.replace(/[^0-9+]/g, "");
        tel.textContent = "Tel. " + m.telefon;
        kontakt.append(tel);
      }
      fuss.append(kontakt);
    }
    const zeile = document.createElement("p");
    zeile.className = "klein";
    zeile.append("© " + new Date().getFullYear() + " " + (m.name || ""));
    for (const [titel, adresse] of [["Impressum", m.impressum], ["Datenschutz", m.datenschutz]]) {
      if (!adresse) continue;
      const verweis = document.createElement("a");
      verweis.href = adresse;
      verweis.target = "_blank";
      verweis.rel = "noopener";
      verweis.textContent = titel;
      zeile.append(" · ", verweis);
    }
    fuss.append(zeile);
    fuss.hidden = false;
  }
})();
