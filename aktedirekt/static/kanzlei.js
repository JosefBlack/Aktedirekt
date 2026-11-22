"use strict";
// Ansicht der Kanzlei: was Mandanten abgeschickt haben, mit allen Angaben,
// Anlagen und der Unterschrift - zum Lesen und zum Ausdrucken für die Akte.
// Die Sitzung lebt nur in diesem Fenster.

const MERKER = "aktedirekt-kanzlei";
const $ = (s) => document.querySelector(s);
let sitzung = "";

function teil(art, klasse, text) {
  const e = document.createElement(art);
  if (klasse) e.className = klasse;
  if (text !== undefined) e.textContent = text;
  return e;
}

function melden(text) {
  $("#meldung").textContent = text || "";
  $("#meldung").hidden = !text;
}

async function aufruf(verfahren, pfad, koerper, roh) {
  const kopf = {};
  if (sitzung) kopf.Authorization = "Bearer " + sitzung;
  if (koerper) kopf["Content-Type"] = "application/json";
  let antwort;
  try {
    antwort = await fetch(pfad, { method: verfahren, headers: kopf, body: koerper ? JSON.stringify(koerper) : undefined });
  } catch (e) {
    throw new Error("Keine Verbindung. Bitte später noch einmal versuchen.");
  }
  if (antwort.ok && roh) return antwort;
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
  const [j, m, t] = iso.slice(0, 10).split("-");
  return t + "." + m + "." + j;
}

function zeitpunkt(iso) {
  return iso ? datum(iso) + (iso.length > 10 ? ", " + iso.slice(11, 16) + " Uhr" : "") : "";
}

function frist_marke(e) {
  if (!e.klagefrist) return null;
  const tage = e.tage_bis_frist;
  const wann = tage < 0 ? "abgelaufen" : tage === 0 ? "heute" : tage === 1 ? "morgen" : "in " + tage + " Tagen";
  return teil("span", "marke " + (tage <= 7 ? "eilt" : "laeuft"), "Klagefrist " + datum(e.klagefrist) + " · " + wann);
}

function ansicht(name) {
  for (const id of ["anmeldung", "liste", "eingang"]) $("#" + id).hidden = id !== name;
  $("#zurueck").hidden = name !== "eingang";
  $("#abmelden").hidden = name === "anmeldung";
  $("#titel").textContent = name === "eingang" ? "Eingang" : "Eingänge";
  window.scrollTo(0, 0);
}

function gescheitert(fehler) {
  if (fehler.status === 401) return abgemeldet("Bitte erneut anmelden.");
  melden(fehler.message);
}

async function liste_laden() {
  const daten = await aufruf("GET", "/api/kanzlei/eingaenge");
  const liste = $("#eingaenge");
  liste.textContent = "";
  for (const e of daten.eingaenge) {
    const li = teil("li", "eingang");
    li.tabIndex = 0;
    li.append(teil("span", "wer", e.mandant || "(ohne Namen)"));
    const stand = teil("div", "stand");
    stand.append(frist_marke(e) || teil("span", "marke laeuft", e.art));
    stand.append(teil("span", "seit", "eingegangen " + zeitpunkt(e.eingegangen_am)));
    li.append(stand);
    li.append(teil("span", "kfz", e.art + (e.vermittler ? " · über " + e.vermittler : "") + (e.in_jurnodes ? " · in jur|nodes" : " · noch nicht in jur|nodes")));
    if (e.sofort === false) li.append(teil("span", "warnung-sofort", "Sofortiger Beginn nicht verlangt – Widerrufsfrist bis " + datum(e.widerrufsfrist_bis)));
    const n = (e.nachrichten || []).length;
    if (n) li.append(teil("span", "nachricht", (n === 1 ? "1 Nachricht" : n + " Nachrichten") + " des Mandanten, zuletzt " + zeitpunkt(e.nachrichten[n - 1])));
    const oeffnen = () => eingang_laden(e.fall).catch(gescheitert);
    li.addEventListener("click", oeffnen);
    li.addEventListener("keydown", (ev) => { if (ev.key === "Enter") oeffnen(); });
    liste.append(li);
  }
  const alt = await aufruf("GET", "/api/kanzlei/entwuerfe");
  $("#vorschlag").hidden = !alt.anzahl;
  $("#vorschlag-text").textContent = (alt.anzahl === 1 ? "Ein nie abgeschickter Entwurf ist" : alt.anzahl + " nie abgeschickte Entwürfe sind")
    + " älter als " + alt.frist_tage + " Tage. Sollen " + (alt.anzahl === 1 ? "er" : "sie") + " gelöscht werden?";
  $("#vorschlag-haken").checked = false;
  $("#vorschlag-loeschen").disabled = true;
  const nachweise = (await aufruf("GET", "/api/kanzlei/loeschungen")).loeschungen;
  $("#nachweis").hidden = !nachweise.length;
  $("#loeschungen").textContent = "";
  for (const n of nachweise) {
    $("#loeschungen").append(teil("li", "", zeitpunkt(n.zeitpunkt) + " · " + n.art + (n.abgeschickt ? "" : " (nicht abgeschickt)")
      + " · " + n.dateien + (n.dateien === 1 ? " Anlage" : " Anlagen") + " · " + n.wer));
  }
  $("#leer").hidden = daten.eingaenge.length > 0;
  $("#anzahl").textContent = daten.eingaenge.length ? daten.eingaenge.length + (daten.eingaenge.length === 1 ? " Eingang" : " Eingänge") : "";
  ansicht("liste");
}

function zeilen(liste) {
  const dl = teil("dl");
  for (const [bezeichnung, wert] of liste) dl.append(teil("dt", "", bezeichnung), teil("dd", "", wert));
  return dl;
}

async function datei_oeffnen(fall, datei) {
  melden("");
  const antwort = await aufruf("GET", "/api/kanzlei/eingaenge/" + fall + "/dateien/" + datei.kennung, null, true);
  // In AWS kommt eine Adresse, unter der die Datei fünf Minuten lang abzuholen ist; sonst die Datei selbst.
  if ((antwort.headers.get("content-type") || "").includes("application/json")) {
    window.open((await antwort.json()).adresse, "_blank", "noopener");
    return;
  }
  const verweis = document.createElement("a");
  verweis.href = URL.createObjectURL(await antwort.blob());
  verweis.download = datei.name;
  verweis.click();
  setTimeout(() => URL.revokeObjectURL(verweis.href), 60000);
}

// Hat der Mandant seinen Link verloren (neues Handy, Verlauf gelöscht), bekommt er
// hier einen neuen - per SMS, WhatsApp oder E-Mail aus der Kanzlei verschickt.
function zugang_bauen(fall) {
  const kasten = teil("div", "anfordern nicht-drucken");
  kasten.append(teil("h2", "", "Neuer Zugang für den Mandanten"));
  kasten.append(teil("p", "klein", "Wenn der Mandant seine Seite nicht mehr findet: Link erzeugen und ihm schicken. "
    + "Er gilt eine Woche und nur einmal; der bisherige Zugang erlischt, sobald der Mandant den neuen Link öffnet."));
  const knopf = teil("button", "schlicht rand", "Neuen Link erzeugen");
  knopf.type = "button";
  const ergebnis = teil("div", "neuer-zugang");
  ergebnis.hidden = true;
  knopf.addEventListener("click", async () => {
    knopf.disabled = true;
    try {
      const z = await aufruf("POST", "/api/kanzlei/eingaenge/" + fall + "/zugang");
      ergebnis.textContent = "";
      const feld = document.createElement("input");
      feld.type = "text";
      feld.readOnly = true;
      feld.value = z.link;
      const kopieren = teil("button", "schlicht rand", "Kopieren");
      kopieren.type = "button";
      kopieren.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(z.link); kopieren.textContent = "Kopiert"; }
        catch (e) { feld.select(); }
      });
      const bild = document.createElement("img");
      bild.src = z.qr;
      bild.alt = "QR-Code mit dem neuen Link";
      bild.className = "qr";
      ergebnis.append(feld, kopieren, teil("p", "klein", "Gültig bis " + zeitpunkt(z.gueltig_bis) + ". Zum Abscannen, wenn der Mandant in der Kanzlei ist:"), bild);
      ergebnis.hidden = false;
    } catch (fehler) { gescheitert(fehler); }
    knopf.disabled = false;
  });
  kasten.append(knopf, ergebnis);
  return kasten;
}

// Unterlagen beim Mandanten anfordern: Er sieht die Punkte oben auf seiner Seite.
function anforderung_bauen(fall, e) {
  const kasten = teil("div", "anfordern nicht-drucken");
  kasten.append(teil("h2", "", "Unterlagen beim Mandanten anfordern"));
  kasten.append(teil("p", "klein", "Der Mandant sieht die gewählten Punkte und Ihren Satz oben auf seiner Seite und kann dort direkt hochladen."
    + (e.anforderung && e.anforderung.am ? " Zuletzt angefordert: " + zeitpunkt(e.anforderung.am) + "." : "")));
  const gewaehlt = new Set((e.anforderung && e.anforderung.schluessel) || []);
  for (const u of e.checkliste || []) {
    const marke = document.createElement("label");
    marke.className = "haken";
    const kasten_ = document.createElement("input");
    kasten_.type = "checkbox";
    kasten_.value = u.schluessel;
    kasten_.checked = gewaehlt.has(u.schluessel);
    marke.append(kasten_, teil("span", "", u.titel + " · " + (u.anzahl ? u.anzahl + " vorhanden" : "nichts vorhanden")
      + (u.angefordert ? (u.nachgereicht ? " · seit Anforderung nachgereicht" : " · angefordert, noch nichts Neues") : "")));
    kasten.append(marke);
  }
  const satz = document.createElement("input");
  satz.type = "text";
  satz.maxLength = 500;
  satz.placeholder = "Optional: ein Satz dazu, z. B. „Bitte auch die Rückseite.“";
  satz.value = (e.anforderung && e.anforderung.text) || "";
  kasten.append(satz);
  const knopf = teil("button", "schlicht rand", "Anforderung speichern");
  knopf.type = "button";
  const ok = teil("span", "klein ok", "");
  knopf.addEventListener("click", async () => {
    knopf.disabled = true;
    try {
      const punkte = Array.from(kasten.querySelectorAll("input[type=checkbox]:checked")).map((k) => k.value);
      const antwort = await aufruf("PUT", "/api/kanzlei/eingaenge/" + fall + "/anforderung", { punkte, text: satz.value });
      ok.textContent = antwort.anforderung ? " Gespeichert – der Mandant sieht es." : " Zurückgenommen.";
    } catch (fehler) { gescheitert(fehler); }
    knopf.disabled = false;
  });
  kasten.append(knopf, ok);
  return kasten;
}

async function eingang_laden(fall) {
  melden("");
  const e = await aufruf("GET", "/api/kanzlei/eingaenge/" + fall);
  const blatt = $("#blatt");
  blatt.textContent = "";
  blatt.append(teil("h1", "", e.kurzrubrum || e.mandant));
  blatt.append(teil("p", "kopfzeile", e.art + " · eingegangen " + zeitpunkt(e.eingegangen_am) + (e.vermittler ? " · über " + e.vermittler : "")));
  const marke = frist_marke(e);
  if (marke) blatt.append(marke);

  const kopf = [["Stand", e.sachstand], ["In jur|nodes angelegt", e.in_jurnodes ? "ja" : "nein"]];
  kopf.push(["Sofortiger Beginn verlangt", e.sofort ? "ja" : "nein – vor dem " + datum(e.widerrufsfrist_bis) + " nur mit Zustimmung tätig werden"]);
  if (e.klagefrist) kopf.unshift(["Klagefrist spätestens", datum(e.klagefrist) + " (drei Wochen ab dem vom Mandanten genannten Zugang; nicht geprüft)"]);
  if (e.vermittler) kopf.push(["Auskunft an " + e.vermittler, e.einwilligung_partner ? "eingewilligt" : "nicht eingewilligt oder widerrufen"]);
  blatt.append(teil("h2", "", "Übersicht"), zeilen(kopf));

  for (const t of e.teile) {
    blatt.append(teil("h2", "", t.titel));
    blatt.append(t.zeilen.length ? zeilen(t.zeilen) : teil("p", "leer", "keine Angaben"));
  }

  blatt.append(teil("h2", "", "Anlagen"));
  if (e.dateien.length) {
    const anlagen = teil("ul", "anlagen");
    for (const d of e.dateien) {
      const li = teil("li");
      li.append(teil("span", "", d.name + " (" + Math.max(1, Math.round(d.groesse / 1024)) + " KB)" + (d.von ? " · eingereicht von " + d.von : "")));
      const knopf = teil("button", "schlicht rand", "Öffnen");
      knopf.type = "button";
      knopf.addEventListener("click", () => datei_oeffnen(fall, d).catch(gescheitert));
      li.append(knopf);
      anlagen.append(li);
    }
    blatt.append(anlagen);
  } else blatt.append(teil("p", "leer", "keine"));

  blatt.append(anforderung_bauen(fall, e));
  blatt.append(zugang_bauen(fall));

  blatt.append(teil("h2", "", "Auftrag und Unterschrift"));
  blatt.append(zeilen([["Unterschrieben am", zeitpunkt(e.nachweis.zeitpunkt)], ["Gerät", e.nachweis.geraet]]));
  if (e.unterschrift) {
    const rahmen = teil("div", "unterschrift");
    const bild = document.createElement("img");
    bild.src = e.unterschrift;
    bild.alt = "Unterschrift des Mandanten";
    rahmen.append(bild);
    blatt.append(rahmen);
  }
  offen = fall;
  $("#loeschen-frage").hidden = true;
  $("#loeschen-oeffnen").hidden = false;
  $("#loeschen-haken").checked = false;
  $("#loeschen-ausfuehren").disabled = true;
  $("#loeschen-jurnodes").textContent = e.in_jurnodes
    ? "Der Vorgang in jur|nodes bleibt davon unberührt."
    : "In jur|nodes ist dieser Eingang noch nicht angelegt – nach dem Löschen gibt es ihn nirgends mehr.";
  ansicht("eingang");
}

$("#vorschlag-haken").addEventListener("change", (e) => { $("#vorschlag-loeschen").disabled = !e.target.checked; });
$("#vorschlag-loeschen").addEventListener("click", async () => {
  $("#vorschlag-loeschen").disabled = true;
  try {
    await aufruf("POST", "/api/kanzlei/entwuerfe/loeschen", { endgueltig: true });
    await liste_laden();
  } catch (fehler) { gescheitert(fehler); }
});

let offen = "";
$("#loeschen-oeffnen").addEventListener("click", () => {
  $("#loeschen-frage").hidden = false;
  $("#loeschen-oeffnen").hidden = true;
});
$("#loeschen-abbrechen").addEventListener("click", () => {
  $("#loeschen-frage").hidden = true;
  $("#loeschen-oeffnen").hidden = false;
});
$("#loeschen-haken").addEventListener("change", (e) => { $("#loeschen-ausfuehren").disabled = !e.target.checked; });
$("#loeschen-ausfuehren").addEventListener("click", async () => {
  $("#loeschen-ausfuehren").disabled = true;
  try {
    await aufruf("DELETE", "/api/kanzlei/eingaenge/" + offen, { endgueltig: true });
    await liste_laden();
  } catch (fehler) { gescheitert(fehler); }
});

function abgemeldet(text) {
  sitzung = "";
  try { sessionStorage.removeItem(MERKER); } catch (e) { /* egal */ }
  $("#eingaenge").textContent = "";
  $("#blatt").textContent = "";
  ansicht("anmeldung");
  melden(text || "");
}

$("#anmeldung").addEventListener("submit", async (e) => {
  e.preventDefault();
  melden("");
  const form = e.currentTarget;
  form.querySelector("button").disabled = true;
  try {
    const daten = await anmeldung_schritt(form, aufruf);
    if (!daten) { form.querySelector("button").disabled = false; return; }
    sitzung = daten.sitzung;
    try { sessionStorage.setItem(MERKER, sitzung); } catch (e2) { /* egal */ }
    anmeldung_zuruecksetzen(form);
    await liste_laden();
  } catch (fehler) {
    if (fehler.status === 403) abgemeldet("Diese Kennung gehört nicht zur Kanzlei.");
    else melden(fehler.message);
  }
  form.querySelector("button").disabled = false;
});

$("#abmelden").addEventListener("click", async () => {
  try { await aufruf("POST", "/api/partner/abmeldung"); } catch (e) { /* die Sitzung läuft ohnehin ab */ }
  abgemeldet();
});
$("#zurueck").addEventListener("click", () => liste_laden().catch(gescheitert));
$("#drucken").addEventListener("click", () => window.print());

(async function beginnen() {
  try { sitzung = sessionStorage.getItem(MERKER) || ""; } catch (e) { sitzung = ""; }
  if (!sitzung) return abgemeldet();
  try { await liste_laden(); }
  catch (fehler) { abgemeldet(fehler.status === 401 || fehler.status === 403 ? "Bitte erneut anmelden." : fehler.message); }
})();
