"""
Client per i dati idrometrici in tempo reale — equivalente idraulico di
sdrea-motore-sismico/app/ingv_client.py.

*** DIFFERENZA IMPORTANTE RISPETTO A INGV, DA CAPIRE PRIMA DI ANDARE IN
PRODUZIONE ***
Il feed sismico INGV è un unico webservice pubblico, standard (FDSN),
stabile da anni, senza autenticazione: il motore sismico lo chiama
direttamente. I dati idrometrici in tempo reale di ARPAE/Regione
Emilia-Romagna NON hanno un equivalente altrettanto semplice e unificato:
sono pubblicati su più canali (il portale Dext3r di ARPA-SIMC per l'accesso
interattivo/programmatico ai dati della rete idro-pluviometrica regionale,
l'Open Data della Regione Emilia-Romagna, e il servizio "Allerta Meteo
Emilia-Romagna" della Protezione Civile regionale per i bollettini di
criticità). Nessuno di questi è stato verificato end-to-end in questa
sessione di sviluppo (né la sandbox ha accesso di rete a questi domini, né
è stato scelto — a monte, dall'ingegnere — quale dei tre canali usare in
produzione, con quali credenziali/quota). Questo è dichiarato esplicitamente
qui invece di far finta che sia già collegato: è il lavoro concreto da fare
per rendere questo motore "reale" come il motore sismico.

Questo modulo è quindi scritto per essere agnostico rispetto alla fonte
esatta: `fetch_recent_levels()` chiama un endpoint HTTP configurabile
(`ARPA_IDRO_BASE_URL`) che deve rispondere con l'elenco di letture nel
formato JSON descritto sotto — è compito dell'ingegnere, una volta scelto
il canale ARPA/Regione ER definitivo, o (a) mappare la risposta reale di
quel canale in questo formato con un piccolo adattatore in questo stesso
file, oppure (b) esporre un piccolo servizio-ponte proprio che normalizza i
dati dalla fonte scelta in questo formato. La logica a valle (parsing,
filtro per corso d'acqua, stima priorità, invio a Ponti-Daghi-Edifici) è
già completa e testata contro questo formato — vedi
app/tests/test_arpa_parsing.py — e non cambierà quando la fonte reale verrà
collegata.

Formato JSON atteso (lista di letture):
[
  {
    "codice_stazione": "16000200",
    "nome_stazione": "Ferrara Pontelagoscuro",
    "corso_d_acqua": "Po",
    "lat": 44.8886, "lon": 11.6103,
    "livello_idrometrico_m": 4.2,
    "soglia1_ordinaria_m": 3.0,
    "soglia2_moderata_m": 4.0,
    "soglia3_elevata_m": 5.0,
    "timestamp": "2026-09-22T08:00:00",
    "fonte": "ARPAE"
  },
  ...
]

In alternativa a un endpoint JSON, `parse_arpa_csv()` accetta il formato
CSV con cui ARPA-SIMC pubblica storicamente gli export "dati orari" delle
stazioni idrometriche (intestazione + righe pipe/comma-separated con le
stesse colonne) — utile se il canale scelto in produzione è un export
schedulato invece di un'API REST.
"""

import csv
import io
import os
from datetime import datetime, timezone

import httpx

ARPA_IDRO_BASE_URL = os.environ.get("ARPA_IDRO_BASE_URL", "")  # DA CONFIGURARE — vedi nota sopra
DEFAULT_TIMEOUT_S = 15.0


def _lettura_id(codice_stazione: str, timestamp_iso: str) -> str:
    """Idempotenza: una lettura è identificata univocamente da stazione+istante."""
    return f"{codice_stazione}@{timestamp_iso}"


def _to_evento_dict(raw: dict) -> dict | None:
    try:
        codice_stazione = str(raw["codice_stazione"]).strip()
        lat = float(raw["lat"])
        lon = float(raw["lon"])
        livello = float(raw["livello_idrometrico_m"])
        timestamp_raw = str(raw["timestamp"])
        ts = datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00"))
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    except (KeyError, ValueError, TypeError):
        return None  # riga malformata: scartata silenziosamente, come in ingv_client.py

    def _opt_float(key):
        v = raw.get(key)
        try:
            return float(v) if v not in (None, "") else None
        except (ValueError, TypeError):
            return None

    return {
        "lettura_id": _lettura_id(codice_stazione, timestamp_raw),
        "codice_stazione": codice_stazione,
        "nome_stazione": raw.get("nome_stazione") or None,
        "corso_d_acqua": raw.get("corso_d_acqua") or None,
        "lat_stazione": lat,
        "lon_stazione": lon,
        "livello_idrometrico_m": livello,
        "soglia1_ordinaria_m": _opt_float("soglia1_ordinaria_m"),
        "soglia2_moderata_m": _opt_float("soglia2_moderata_m"),
        "soglia3_elevata_m": _opt_float("soglia3_elevata_m"),
        "timestamp": ts,
        "fonte": raw.get("fonte") or "ARPA",
    }


def parse_arpa_json(payload: list) -> list[dict]:
    """Converte la risposta JSON (lista di dict, vedi docstring del modulo) in righe pronte per EventoIdraulico."""
    eventi = []
    for raw in payload or []:
        parsed = _to_evento_dict(raw)
        if parsed is not None:
            eventi.append(parsed)
    return eventi


def parse_arpa_csv(raw_text: str) -> list[dict]:
    """
    Formato CSV con intestazione (vedi docstring del modulo per le
    colonne attese, stessi nomi delle chiavi JSON). Righe malformate
    vengono scartate singolarmente, non fanno fallire l'intero batch —
    stessa filosofia di ingv_client.parse_ingv_text.
    """
    eventi = []
    reader = csv.DictReader(io.StringIO(raw_text.strip()))
    for raw in reader:
        parsed = _to_evento_dict(raw)
        if parsed is not None:
            eventi.append(parsed)
    return eventi


def fetch_recent_levels() -> list[dict]:
    """
    Interroga l'endpoint configurato in ARPA_IDRO_BASE_URL. Solleva
    un'eccezione esplicita e parlante se non è stato configurato, invece
    di fallire con un errore di rete generico — è la situazione attesa
    finché l'ingegnere non ha scelto e collegato il canale ARPA/Regione ER
    definitivo (vedi nota in cima al modulo).
    """
    if not ARPA_IDRO_BASE_URL:
        raise RuntimeError(
            "ARPA_IDRO_BASE_URL non configurato: nessuna fonte dati idrometrici reale è "
            "ancora collegata. Vedi la nota in app/arpa_client.py su Dext3r/Open Data ER/"
            "Allerta Meteo ER — è il passo da chiudere per rendere il motore idraulico "
            "operativo quanto quello sismico. Nel frattempo usa POST /api/eventi/manuale "
            "per test e demo."
        )
    resp = httpx.get(ARPA_IDRO_BASE_URL, timeout=DEFAULT_TIMEOUT_S)
    resp.raise_for_status()
    return parse_arpa_json(resp.json())
