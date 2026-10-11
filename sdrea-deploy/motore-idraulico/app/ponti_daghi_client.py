"""
Unico punto di contatto con il backend Ponti-Daghi-Edifici — identico
principio del motore sismico: solo via HTTP, mai import di codice tra
backend. Riusa lo stesso `readiness-feed`/`sdrea/eventi` (A.8/A.4): i due
motori (sismico e idraulico) sono due CLIENT diversi dello stesso
contratto, non due contratti diversi — Ponti-Daghi-Edifici non sa (e non
deve sapere) quale dei due motori gli sta scrivendo.
"""

import os

import httpx

PONTI_DAGHI_BASE_URL = os.environ.get("PONTI_DAGHI_BASE_URL", "http://localhost:8000").rstrip("/")
DEFAULT_TIMEOUT_S = 10.0


def get_readiness_feed() -> list[dict]:
    """
    GET /api/sdrea/readiness-feed — include ora anche i campi di
    vulnerabilità idraulica (zona_pgra, quota_slm_m,
    distanza_corso_acqua_km, corso_acqua_riferimento,
    presenza_argine_protezione), aggiunti lato Ponti-Daghi-Edifici
    specificamente per questo motore.
    """
    resp = httpx.get(f"{PONTI_DAGHI_BASE_URL}/api/sdrea/readiness-feed", timeout=DEFAULT_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json().get("strutture", [])


def push_evento_struttura(structure_id: int, tipo_evento: str, data_iso: str, intensita_stimata: float, fonte: str) -> dict:
    """
    POST /api/sdrea/eventi — stesso endpoint del motore sismico,
    `tipo_evento="alluvione"`. `intensita_stimata` qui è il
    `superamento_normalizzato` (vedi app/idrometria.py), non una PGA: il
    campo è generico lato Ponti-Daghi-Edifici proprio per essere
    riutilizzabile da qualunque motore di rischio.
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
