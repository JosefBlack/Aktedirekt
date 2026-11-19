"""Die Aufnahme als PDF für die Akte - ohne fremde Bausteine.

Ein schlichtes Blatt: Überschriften, darunter Bezeichnung und Angabe. Es soll
in der Akte lesbar sein und sonst nichts. Geschrieben wird das PDF von Hand,
weil dafür kein Paket in die Anwendung muss, das seinerseits gepflegt und
geprüft werden will.

Schrift ist Helvetica in der westeuropäischen Belegung; Umlaute und das
Eurozeichen gehen, was es dort nicht gibt, wird zum Fragezeichen.
"""

from __future__ import annotations

BREITE, HOEHE, RAND = 595, 842, 56
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
    inhalt: list[bytes] = []
    y = HOEHE - RAND
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
