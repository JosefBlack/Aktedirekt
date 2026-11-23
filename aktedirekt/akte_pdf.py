"""Die Aufnahme als PDF für die Akte - ohne fremde Bausteine.

Ein schlichtes Blatt: Überschriften, darunter Bezeichnung und Angabe. Es soll
in der Akte lesbar sein und sonst nichts. Geschrieben wird das PDF von Hand,
weil dafür kein Paket in die Anwendung muss, das seinerseits gepflegt und
geprüft werden will.

Schrift ist Helvetica in der westeuropäischen Belegung; Umlaute und das
Eurozeichen gehen, was es dort nicht gibt, wird zum Fragezeichen.
"""

from __future__ import annotations

import functools
import re
from importlib import resources

BREITE, HOEHE, RAND = 595, 842, 56
LOGO_BREITE, LOGO_OBEN = 150, 28
_ZEILE = {"titel": (16, 22, True), "kopf": (9, 13, False), "abschnitt": (12, 24, True),
          "bezeichnung": (8, 11, False), "wert": (10, 14, False), "leer": (10, 8, False)}


def _umbrechen(text: str, groesse: int) -> list[str]:
    """Bricht nach Zeichenzahl um. Helvetica ist im Mittel etwa halb so breit wie hoch."""
    je_zeile = max(20, int((BREITE - 2 * RAND) / (groesse * 0.5)))
    zeilen: list[str] = []
    for absatz in text.replace("\r", "").split("\n"):
        zeile = ""
        for wort in absatz.split(" "):
            while len(wort) > je_zeile:                    # ein Wort, länger als die Zeile
                if zeile:
                    zeilen.append(zeile)
                    zeile = ""
                zeilen.append(wort[:je_zeile])
                wort = wort[je_zeile:]
            if zeile and len(zeile) + 1 + len(wort) > je_zeile:
                zeilen.append(zeile)
                zeile = wort
            else:
                zeile = f"{zeile} {wort}" if zeile else wort
        zeilen.append(zeile)
    return zeilen


def _kodiert(text: str) -> bytes:
    roh = text.encode("cp1252", "replace")
    return roh.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _zahl(wert: float) -> bytes:
    return (b"%.3f" % wert).rstrip(b"0").rstrip(b".")


def _pfad(d: str) -> bytes:
    """SVG-Pfad (nur M, L, C, Z mit absoluten Werten) als PDF-Zeichenbefehle."""
    teile = re.findall(r"[MLCZ]|-?[\d.]+(?:e-?\d+)?", d)
    ops, i, zeichen = [], 0, ""
    anzahl = {"M": 2, "L": 2, "C": 6, "Z": 0}
    befehl = {"M": b"m", "L": b"l", "C": b"c"}
    while i < len(teile):
        if teile[i] in anzahl:
            zeichen = teile[i]
            i += 1
            if zeichen == "Z":
                ops.append(b"h")
                continue
        n = anzahl[zeichen]
        if n == 0 or i + n > len(teile):
            raise ValueError("Pfad nicht lesbar")
        ops.append(b" ".join(_zahl(float(w)) for w in teile[i:i + n]) + b" " + befehl[zeichen])
        i += n
    return b" ".join(ops)


def _farbe(text: str) -> bytes:
    werte = re.findall(r"([\d.]+)%", text)
    if len(werte) != 3:
        raise ValueError("Farbe nicht lesbar")
    return b" ".join(_zahl(float(w) / 100) for w in werte) + b" rg"


@functools.lru_cache(maxsize=1)
def _logo() -> tuple[bytes, float]:
    """Das Logo der Kanzlei (texte/logo.svg) als Zeichenbefehle für die rechte obere Ecke, und seine Höhe.

    Gelesen wird nur die schlichte Form, die etwa ein Export aus einem PDF ergibt:
    Pfade mit Füllfarbe und Schriftzeichen als wiederverwendete Pfade. Alles
    andere - oder gar kein Logo - ergibt ein Blatt ohne Logo.
    """
    datei = resources.files("aktedirekt").joinpath("texte", "logo.svg")
    try:
        svg = datei.read_text("utf-8")
        x0, y0, breite, hoehe = (float(v) for v in re.search(r'viewBox="([^"]+)"', svg).group(1).split())
        zeichen = {m.group(1): _pfad(m.group(2)) for m in
                   re.finditer(r'<g id="([^"]+)">\s*<path[^>]* d="([^"]*)"', svg)}
        rumpf = svg[svg.index("</defs>"):] if "</defs>" in svg else svg
        ops = []
        for m in re.finditer(r'<path([^>]*)/>|<g fill="([^"]*)"[^>]*>(.*?)</g>', rumpf, re.S):
            if m.group(1) is not None:
                attr = m.group(1)
                fill = re.search(r' fill="([^"]*)"', attr)
                if not fill or fill.group(1) == "none":
                    continue
                regel = b"f*" if 'evenodd' in attr else b"f"
                ops.append(_farbe(fill.group(1)) + b" " + _pfad(re.search(r' d="([^"]*)"', attr).group(1)) + b" " + regel)
            else:
                farbe = _farbe(m.group(2))
                for u in re.finditer(r'href="#([^"]+)" x="([-\d.]+)" y="([-\d.]+)"', m.group(3)):
                    if not zeichen.get(u.group(1)):
                        continue                   # etwa ein Leerzeichen: nichts zu zeichnen
                    ops.append(b"q %s 1 0 0 1 %s %s cm %s f Q" % (farbe, _zahl(float(u.group(2))), _zahl(float(u.group(3))),
                                                                zeichen[u.group(1)]))
        if not ops:
            return b"", 0.0
    except (OSError, ValueError, AttributeError, KeyError):
        return b"", 0.0
    massstab = LOGO_BREITE / breite
    links, oben = BREITE - RAND - LOGO_BREITE, HOEHE - LOGO_OBEN
    # SVG zählt von oben nach unten, PDF von unten nach oben: spiegeln und verschieben.
    rahmen = b"q %s 0 0 %s %s %s cm " % (_zahl(massstab), _zahl(-massstab), _zahl(links - x0 * massstab),
                                          _zahl(oben + y0 * massstab))
    return rahmen + b"\n".join(ops) + b" Q", hoehe * massstab


def erzeugen(titel: str, kopfzeilen: list[str], teile: list[dict], anlagen: list[str] | None) -> bytes:
    """``teile`` wie ``Aufnahme.fuer_die_akte()``: Überschrift und Zeilen aus Bezeichnung und Angabe."""
    zeilen: list[tuple[str, str]] = [("titel", z) for z in _umbrechen(titel, 16)]
    zeilen += [("kopf", z) for k in kopfzeilen for z in _umbrechen(k, 9)]
    for teil in teile:
        if teil["titel"]:
            zeilen.append(("abschnitt", teil["titel"]))
        if not teil["zeilen"]:
            zeilen.append(("wert", "keine Angaben"))
        for bezeichnung, wert in teil["zeilen"]:
            if bezeichnung:
                zeilen += [("bezeichnung", z) for z in _umbrechen(bezeichnung, 8)]
            zeilen += [("wert", z) for z in _umbrechen(wert, 10)]
            zeilen.append(("leer", ""))
    if anlagen is not None:                        # ``None``: ein Blatt ohne Anlagenverzeichnis
        zeilen.append(("abschnitt", "Anlagen"))
        zeilen += [("wert", z) for a in (anlagen or ["keine"]) for z in _umbrechen(a, 10)]

    seiten: list[bytes] = []
    logo, logo_hoehe = _logo()
    inhalt: list[bytes] = [logo] if logo else []
    # Mit Logo beginnt die erste Seite unter ihm.
    y = min(HOEHE - RAND, HOEHE - LOGO_OBEN - logo_hoehe - 14) if logo else HOEHE - RAND
    for art, text in zeilen:
        groesse, hoehe, fett = _ZEILE[art]
        if y - hoehe < RAND:
            seiten.append(b"\n".join(inhalt))
            inhalt, y = [], HOEHE - RAND
        y -= hoehe
        if text:
            inhalt.append(b"BT /F%d %d Tf %d %d Td (%s) Tj ET" % (2 if fett else 1, groesse, RAND, y, _kodiert(text)))
    seiten.append(b"\n".join(inhalt))

    # Gegenstände: 1 Verzeichnis, 2 Seitenliste, 3 und 4 Schriften, dann je Seite Blatt und Inhalt.
    gegenstaende: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count %d /Kids [%s] >>" % (len(seiten), b" ".join(b"%d 0 R" % (5 + 2 * i) for i in range(len(seiten)))),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]
    for i, seite in enumerate(seiten):
        gegenstaende.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
                            b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>" % (BREITE, HOEHE, 6 + 2 * i))
        gegenstaende.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(seite), seite))

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    stellen = []
    for nummer, gegenstand in enumerate(gegenstaende, 1):
        stellen.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (nummer, gegenstand)
    verzeichnis = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(gegenstaende) + 1)
    pdf += b"".join(b"%010d 00000 n \n" % s for s in stellen)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(gegenstaende) + 1, verzeichnis)
    return bytes(pdf)


def _fertig(seite: bytes) -> bytes:
    """Ein einseitiges PDF aus einem Inhaltsstrom (Schriften F1 normal, F2 fett)."""
    gegenstaende = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [5 0 R] >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents 6 0 R "
        b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>" % (BREITE, HOEHE),
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(seite), seite),
    ]
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    stellen = []
    for nummer, gegenstand in enumerate(gegenstaende, 1):
        stellen.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (nummer, gegenstand)
    verzeichnis = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(gegenstaende) + 1)
    pdf += b"".join(b"%010d 00000 n \n" % s for s in stellen)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(gegenstaende) + 1, verzeichnis)
    return bytes(pdf)


def _mittig(text: str, groesse: int, y: float, fett: bool = False, farbe: str = "0.09 0.2 0.3") -> bytes:
    """Eine Zeile, waagerecht mittig (Breite geschätzt: Helvetica etwa halb so breit wie hoch)."""
    breite = len(text) * groesse * (0.56 if fett else 0.5)
    return b"%s rg BT /F%d %d Tf %.1f %.1f Td (%s) Tj ET" % (
        farbe.encode(), 2 if fett else 1, groesse, max(RAND / 2, (BREITE - breite) / 2), y, _kodiert(text))


def aushang(kanzlei: str, unterzeile: str, titel: str, untertitel: str, punkte: list[str],
            qr: list[list[int]], partner: str, adresse: str) -> bytes:
    """Ein A4-Aushang für den Tresen der Werkstatt: große Überschrift, QR-Code, drei Punkte."""
    teile: list[bytes] = []
    y = HOEHE - 70
    teile.append(_mittig(kanzlei, 15, y, True))
    if unterzeile:
        teile.append(_mittig(unterzeile, 11, y - 18, False, "0.36 0.41 0.46"))
    teile.append(_mittig(titel, 44, HOEHE - 180, True))
    for i, z in enumerate(_umbrechen(untertitel, 26)[:2]):
        teile.append(_mittig(z, 18, HOEHE - 220 - i * 24, False, "0.2 0.24 0.3"))
    # QR-Code als Vektorgrafik: je dunklem Modul ein Rechteck.
    groesse = 250
    modul = groesse / max(1, len(qr))
    x0, y0 = (BREITE - groesse) / 2, HOEHE - 290 - groesse
    rechtecke = [b"%.2f %.2f %.2f %.2f re" % (x0 + x * modul, y0 + (len(qr) - 1 - zy) * modul, modul + 0.05, modul + 0.05)
                 for zy, reihe in enumerate(qr) for x, an in enumerate(reihe) if an]
    teile.append(b"0 0 0 rg " + b" ".join(rechtecke) + b" f")
    teile.append(_mittig("Mit der Handykamera scannen", 13, y0 - 26, True))
    y = y0 - 70
    for p in punkte:
        teile.append(_mittig("\u2713  " + p if False else "-  " + p, 14, y, False, "0.15 0.2 0.25"))
        y -= 24
    if partner:
        teile.append(_mittig("In Zusammenarbeit mit " + partner, 11, 80, False, "0.36 0.41 0.46"))
    teile.append(_mittig(adresse, 9, 62, False, "0.5 0.55 0.6"))
    return _fertig(b"\n".join(teile))
