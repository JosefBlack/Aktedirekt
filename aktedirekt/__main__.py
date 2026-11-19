"""Start für Entwicklung und Vorführung: ``python -m aktedirekt``.

Bindet nur an diesen Rechner. Mit Testdaten benutzen, nicht mit Mandaten.
"""

import os

import uvicorn

from aktedirekt.web import app_aus_umgebung

if __name__ == "__main__":
    os.environ.setdefault("AKTEDIREKT_PARTNER", "testdaten/partner.json")
    uvicorn.run(app_aus_umgebung(), host="127.0.0.1",
                port=int(os.environ.get("AKTEDIREKT_PORT", "8000")), access_log=False)
