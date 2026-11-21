"""E-Mails an Mandanten - über Amazon SES im Konto der Kanzlei.

Zwei Anlässe: die Eingangsbestätigung nach dem Abschicken (mit Vollmacht,
Widerrufsbelehrung und Hinweisen zur Datenverarbeitung als PDF - damit hat der
Mandant sie auf einem dauerhaften Datenträger) und der Link zum Wiederfinden
seines Falls. In keiner E-Mail stehen Angaben zum Fall: E-Mails reisen
unverschlüsselt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Protocol


class Post(Protocol):
    def senden(self, an: str, betreff: str, text: str,
               anlagen: list[tuple[str, bytes, str]] | None = None) -> None: ...


def nachricht(absender: str, name: str, an: str, betreff: str, text: str,
              anlagen: list[tuple[str, bytes, str]] | None = None) -> bytes:
    """Die E-Mail als Bytes, wie SES sie verschickt."""
    m = EmailMessage()
    m["From"] = formataddr((name, absender)) if name else absender
    m["To"] = an
    m["Subject"] = betreff
    m["Message-ID"] = make_msgid(domain=absender.rpartition("@")[2] or None)
    m.set_content(text)
    for dateiname, inhalt, art in anlagen or []:
        haupt, _, unter = art.partition("/")
        m.add_attachment(inhalt, maintype=haupt, subtype=unter, filename=dateiname)
    return m.as_bytes()


class SesPost:
    def __init__(self, absender: str, name: str = "", region: str = "eu-central-1") -> None:
        import boto3
        self._ses = boto3.client("sesv2", region_name=region)
        self._absender, self._name = absender, name

    def senden(self, an: str, betreff: str, text: str,
               anlagen: list[tuple[str, bytes, str]] | None = None) -> None:
        self._ses.send_email(FromEmailAddress=self._absender, Destination={"ToAddresses": [an]},
                             Content={"Raw": {"Data": nachricht(self._absender, self._name, an, betreff, text, anlagen)}})


@dataclass
class Postfach:
    """Für Prüfungen und die Vorführung: merkt sich, was verschickt würde."""
    gesendet: list[dict] = field(default_factory=list)

    def senden(self, an: str, betreff: str, text: str,
               anlagen: list[tuple[str, bytes, str]] | None = None) -> None:
        self.gesendet.append({"an": an, "betreff": betreff, "text": text, "anlagen": anlagen or []})
