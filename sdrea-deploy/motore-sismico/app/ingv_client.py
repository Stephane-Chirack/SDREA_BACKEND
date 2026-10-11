"""
B.2 — Client per il feed pubblico eventi sismici INGV.

Usa il webservice standard FDSN event, implementato pubblicamente da INGV:
    GET https://webservices.ingv.it/fdsnws/event/1/query
        ?starttime=...&minmagnitude=...&format=text

`format=text` ritorna righe pipe-separated con questo schema (documentato
da INGV, stabile da anni):
    EventID|Time|Latitude|Longitude|Depth/Km|Author|Catalog|Contributor|
    ContributorID|MagType|Magnitude|MagAuthor|EventLocationName|EventType

Vedi README.md per la nota importante: questa sandbox di sviluppo non ha
accesso di rete a webservices.ingv.it (proxy di egress dell'ambiente lo
rifiuta), quindi la chiamata HTTP reale non è stata osservata funzionare
in questa sessione — il parsing invece sì, contro il formato di testo
documentato (vedi app/tests/test_ingv_parsing.py).
"""

from datetime import datetime, timezone

import httpx

INGV_BASE_URL = "https://webservices.ingv.it/fdsnws/event/1/query"
DEFAULT_TIMEOUT_S = 15.0


def parse_ingv_text(raw_text: str) -> list[dict]:
    """
    Converte il formato text pipe-separated INGV in una lista di dict con
    le chiavi già nel formato di EventoSismico. Righe malformate vengono
    scartate silenziosamente (con un motivo tracciabile via logging, se
    servisse in futuro) invece di far fallire l'intero batch per un
    singolo evento con un campo mancante.
    """
    eventi = []
    lines = [l.strip() for l in raw_text.strip().splitlines() if l.strip()]
    if not lines:
        return eventi

    # La prima riga è l'intestazione ("#EventID|Time|...") — la saltiamo
    # se inizia con '#' o se il primo campo non è numerico/id plausibile.
    for line in lines:
        if line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) < 13:
            continue
        try:
            event_id = parts[0].strip()
            time_str = parts[1].strip()
            lat = float(parts[2])
            lon = float(parts[3])
            depth_km = float(parts[4]) if parts[4].strip() else None
            magnitude = float(parts[10])
            location_name = parts[12].strip()
            # Il timestamp INGV è ISO 8601 UTC, es. "2026-09-20T14:32:10.123456"
            ts = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            if ts.tzinfo is not None:
                ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
        except (ValueError, IndexError):
            continue

        eventi.append({
            "ingv_event_id": event_id,
            "magnitudo": magnitude,
            "lat_epicentro": lat,
            "lon_epicentro": lon,
            "profondita_km": depth_km,
            "timestamp": ts,
            "fonte": "INGV",
            "luogo": location_name or None,
        })
    return eventi


def fetch_recent_events(starttime_iso: str, min_magnitude: float = 2.0) -> list[dict]:
    """
    Interroga il webservice INGV reale. Ritorna una lista (vuota se
    nessun evento, mai None) — se il servizio non è raggiungibile (rete
    assente, timeout, errore HTTP) rilancia l'eccezione: chi chiama decide
    se loggare e ritentare al prossimo poll invece che far cadere in
    silenzio un errore di rete che varrebbe la pena notare.
    """
    params = {
        "starttime": starttime_iso,
        "minmagnitude": min_magnitude,
        "format": "text",
    }
    resp = httpx.get(INGV_BASE_URL, params=params, timeout=DEFAULT_TIMEOUT_S)
    resp.raise_for_status()
    return parse_ingv_text(resp.text)
