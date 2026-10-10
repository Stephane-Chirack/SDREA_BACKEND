"""
Unico punto di contatto con il backend Ponti-Daghi-Edifici — solo via
HTTP, mai import di codice tra i due backend (principio architetturale
non negoziabile della spec).
"""

import os

import httpx

PONTI_DAGHI_BASE_URL = os.environ.get("PONTI_DAGHI_BASE_URL", "http://localhost:8000").rstrip("/")
DEFAULT_TIMEOUT_S = 10.0

# Stessa chiave condivisa impostata lato Ponti-Daghi-Edifici come
# SDREA_SERVICE_KEY (vedi app/auth.py:make_service_auth_dependency in quel
# backend): se non impostata qui, semplicemente non viene inviato nessun
# header e il canale resta quello di oggi (aperto, finché entrambi i
# servizi non hanno la variabile configurata).
_SERVICE_KEY = os.environ.get("PONTI_DAGHI_SERVICE_KEY")


def _service_headers() -> dict:
    return {"X-SDREA-Service-Key": _SERVICE_KEY} if _SERVICE_KEY else {}


def diagnostica_canale() -> dict:
    """
    Stato del canale verso Ponti-Daghi-Edifici, pensato per essere letto da
    un endpoint di diagnostica (vedi main.py) invece che ricostruito a mano
    confrontando screenshot: non espone mai la chiave intera, solo lunghezza
    e ultimi caratteri (sufficienti per verificare un copia-incolla sbagliato
    senza rivelare il segreto), più l'esito di una chiamata reale di prova.
    """
    info = {
        "base_url": PONTI_DAGHI_BASE_URL,
        "chiave_configurata": bool(_SERVICE_KEY),
        "chiave_lunghezza": len(_SERVICE_KEY) if _SERVICE_KEY else None,
        "chiave_ultimi_4": _SERVICE_KEY[-4:] if _SERVICE_KEY else None,
    }
    try:
        resp = httpx.get(
            f"{PONTI_DAGHI_BASE_URL}/api/sdrea/readiness-feed",
            headers=_service_headers(),
            timeout=DEFAULT_TIMEOUT_S,
        )
        info["test_readiness_feed_status"] = resp.status_code
        info["test_readiness_feed_ok"] = resp.status_code == 200
        if resp.status_code != 200:
            info["test_readiness_feed_dettaglio"] = resp.text[:300]
    except Exception as exc:
        info["test_readiness_feed_ok"] = False
        info["test_readiness_feed_errore"] = f"{type(exc).__name__}: {exc}"
    return info


def get_readiness_feed() -> list[dict]:
    """
    GET /api/sdrea/readiness-feed su Ponti-Daghi-Edifici (contratto A.8).
    Ritorna la lista "strutture". Rilancia l'eccezione HTTP/rete al
    chiamante: il motore di priorità non ha senso calcolato su dati
    parziali/vecchi senza che qualcuno lo sappia.
    """
    resp = httpx.get(
        f"{PONTI_DAGHI_BASE_URL}/api/sdrea/readiness-feed",
        headers=_service_headers(),
        timeout=DEFAULT_TIMEOUT_S,
    )
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
    resp = httpx.post(
        f"{PONTI_DAGHI_BASE_URL}/api/sdrea/eventi",
        json=payload,
        headers=_service_headers(),
        timeout=DEFAULT_TIMEOUT_S,
    )
    resp.raise_for_status()
    return resp.json()
