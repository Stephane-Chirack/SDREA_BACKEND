"""
Unico punto di contatto con il backend Ponti-Daghi-Edifici — solo via
HTTP, mai import di codice tra i due backend (principio architetturale
non negoziabile della spec).
"""

import os

import httpx

PONTI_DAGHI_BASE_URL = os.environ.get("PONTI_DAGHI_BASE_URL", "http://localhost:8000").rstrip("/")
DEFAULT_TIMEOUT_S = 10.0


def get_readiness_feed() -> list[dict]:
    """
    GET /api/sdrea/readiness-feed su Ponti-Daghi-Edifici (contratto A.8).
    Ritorna la lista "strutture". Rilancia l'eccezione HTTP/rete al
    chiamante: il motore di priorità non ha senso calcolato su dati
    parziali/vecchi senza che qualcuno lo sappia.
    """
    resp = httpx.get(f"{PONTI_DAGHI_BASE_URL}/api/sdrea/readiness-feed", timeout=DEFAULT_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json().get("strutture", [])


def push_evento_struttura(structure_id: int, tipo_evento: str, data_iso: str, intensita_stimata: float, fonte: str) -> dict:
    """
    POST /api/sdrea/eventi su Ponti-Daghi-Edifici — popola EventoStruttura
    (A.4) lato loro. Ritorna il body della risposta; solleva
    HTTPStatusError se il push fallisce (chi chiama decide come gestirlo:
    vedi main.py, che registra il fallimento invece di far fallire in
    silenzio l'intera valutazione di priorità già calcolata qui).
    """
    payload = {
        "structure_id": structure_id,
        "tipo_evento": tipo_evento,
        "data": data_iso,
        "intensita_stimata": intensita_stimata,
        "fonte": fonte,
    }
    resp = httpx.post(f"{PONTI_DAGHI_BASE_URL}/api/sdrea/eventi", json=payload, timeout=DEFAULT_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()
