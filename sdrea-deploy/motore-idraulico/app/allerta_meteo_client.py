"""
Client per la API pubblica ufficiale di Allerta Meteo Emilia-Romagna
(Agenzia regionale per la sicurezza territoriale e la protezione civile) —
FONTE REALE E VERIFICATA IN QUESTA SESSIONE, non solo documentata.

A differenza di app/arpa_client.py (che resta un adattatore configurabile
perché nessuna fonte idrometrica per-stazione con soglie ufficiali è stata
trovata con una API pubblica stabile), i tre endpoint qui sotto sono stati
interrogati direttamente durante lo sviluppo e hanno risposto con dati reali
e correnti, senza autenticazione:

- GET https://allertameteo.regione.emilia-romagna.it/o/get-stato-allerta
  Stato di allerta corrente per zona di allertamento (colori standard
  Protezione Civile: green/yellow/orange/red) e per fenomeno (idraulica,
  idrogeologica, temporali, vento, temperature_estreme, mareggiate,
  stato_mare), più il bollettino di vigilanza collegato (titolo, link PDF,
  validità). Verificato il 22/09/2026: risposta reale con bollettino
  144/2026 in corso di validità.

- GET https://allertameteo.regione.emilia-romagna.it/o/get-bollettino-monitoraggio
  Ultimi bollettini di monitoraggio idrologico: per ciascun bacino
  idrografico monitorato (~29 bacini principali dell'Emilia-Romagna, da
  Trebbia/Taro/Secchia/Panaro/Reno fino a Marecchia/Conca), le letture
  osservate più recenti per sottobacino/stazione (valore e ora) e, quando
  disponibile, il colmo di piena previsto. Verificato il 22/09/2026:
  risposta reale con bollettino 010/2025/04ULTIMO, bacino SENIO con 6
  sottobacini e letture numeriche reali.

  *** LIMITE DICHIARATO, DA VALIDARE ***
  Questo bollettino NON fornisce lat/lon né soglie1/2/3 ufficiali per
  sottobacino/stazione — solo un nome (es. "Fusignano", "Tebano") e un
  valore. Non è quindi collegabile 1:1 al formato di app/arpa_client.py
  (pensato per una stazione con coordinate e soglie) senza una tabella di
  mappatura nome-stazione -> coordinate/soglie, che va costruita e
  validata a parte (idealmente con ARPAE/l'Agenzia regionale stessa, che
  conosce l'anagrafica esatta delle stazioni). Per questo le letture di
  questo bollettino sono salvate in un modello dedicato
  (LetturaBollettinoMonitoraggio, vedi app/models.py), non forzate dentro
  EventoIdraulico: è un secondo segnale ufficiale reale, distinto e
  utilizzabile da Vicky per incrociare i dati, non un sostituto del feed
  per-stazione con soglie.

- GET https://allertameteo.regione.emilia-romagna.it/o/xmlcap
  Stesso stato di allerta in formato CAP (Common Alerting Protocol,
  standard internazionale), utile come fonte interoperabile/di backup se
  in futuro serve integrarsi con altri sistemi di allertamento.

Nessuno di questi tre endpoint richiede API key o registrazione (verificato
anche nella pagina sviluppatori del portale: /sviluppatori).
"""

import os
from datetime import datetime, timezone

import httpx

ALLERTA_METEO_BASE_URL = os.environ.get(
    "ALLERTA_METEO_ER_BASE_URL", "https://allertameteo.regione.emilia-romagna.it"
)
DEFAULT_TIMEOUT_S = 15.0

# Mappatura dei colori restituiti dalla API (inglese) sulla scala standard
# italiana di Protezione Civile usata nel resto del motore idraulico
# (vedi app/idrometria.py, LIVELLI_ALLERTA in app/models.py).
_COLORE_EN_IT = {
    "green": "verde",
    "yellow": "giallo",
    "orange": "arancione",
    "red": "rosso",
    None: "sconosciuto",
}


def _colore_it(colore_en) -> str:
    return _COLORE_EN_IT.get(colore_en, "sconosciuto")


def fetch_stato_allerta(data: str | None = None) -> dict:
    """
    Interroga /o/get-stato-allerta. `data` opzionale nel formato
    "YYYY-MM-DD HH:mm" (vedi pagina /sviluppatori) per uno stato passato;
    se omesso, ritorna lo stato corrente.
    """
    params = {"data": data} if data else {}
    resp = httpx.get(f"{ALLERTA_METEO_BASE_URL}/o/get-stato-allerta", params=params, timeout=DEFAULT_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


def parse_stato_allerta(payload: dict, fetch_at: datetime | None = None) -> list[dict]:
    """
    Converte la risposta di /o/get-stato-allerta in una lista di righe,
    una per (zona, fenomeno) — pronta per StatoAllertaZona (app/models.py).

    Formato reale osservato (22/09/2026):
        {
          "dataEmissione": "Sep 22, 2026 12:01:40 PM",
          "titolo": "Bollettino di vigilanza 144/2026 valido dalle 00:00 del 23/09/2026: nessuna allerta.",
          "link": "/documents/.../bollettino144_2026.pdf",
          "dataInizio": "...", "dataFine": "...",
          "descrizionemeteo": "...",
          "allerte": {
            "H1": {"idraulica": "green", "idrogeologica": "green", ...},
            "G1": {...}, ...
          }
        }
    Righe malformate (zona senza dict, fenomeno senza stringa colore) sono
    scartate singolarmente, stessa filosofia di arpa_client/ingv_client.
    """
    fetch_at = fetch_at or datetime.now(timezone.utc).replace(tzinfo=None)
    data_emissione_raw = payload.get("dataEmissione")
    titolo = payload.get("titolo")
    link_bollettino = payload.get("link")
    data_inizio_raw = payload.get("dataInizio")
    data_fine_raw = payload.get("dataFine")

    righe = []
    allerte = payload.get("allerte") or {}
    for zona, fenomeni in allerte.items():
        if not isinstance(fenomeni, dict):
            continue
        for fenomeno, colore_en in fenomeni.items():
            if not isinstance(fenomeno, str):
                continue
            righe.append({
                "zona": str(zona),
                "fenomeno": fenomeno,
                "colore": _colore_it(colore_en),
                "colore_raw": colore_en,
                "titolo_bollettino": titolo,
                "link_bollettino": link_bollettino,
                "data_emissione_raw": data_emissione_raw,
                "data_inizio_raw": data_inizio_raw,
                "data_fine_raw": data_fine_raw,
                "fetch_at": fetch_at,
                "fonte": "Allerta Meteo Emilia-Romagna (Agenzia regionale protezione civile)",
            })
    return righe


def fetch_bollettino_monitoraggio() -> dict:
    """Interroga /o/get-bollettino-monitoraggio (nessun parametro)."""
    resp = httpx.get(f"{ALLERTA_METEO_BASE_URL}/o/get-bollettino-monitoraggio", timeout=DEFAULT_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


def parse_bollettino_monitoraggio(payload: dict, fetch_at: datetime | None = None) -> list[dict]:
    """
    Converte la risposta di /o/get-bollettino-monitoraggio in una lista di
    letture per sottobacino — pronta per LetturaBollettinoMonitoraggio
    (app/models.py). Il payload è un dict con chiave = id bollettino
    (es. "010/2025/04ULTIMO") e valore = {bacino: [...], inizioMonitoraggio,
    fineMonitoraggio, note}; ogni bacino ha nome_bacino, comuni, province,
    sottobacino: [{nomeSottobacino, osservazione: {dataOra, valore},
    colmo_previsto: {dataOra, valore}}].

    `valore` arriva come stringa numerica (es. "4.06", "-1.62") o null: la
    scala/unità non è dichiarata dall'endpoint (verosimilmente livello in
    metri riferito allo zero idrometrico locale della sezione, coerente
    con l'uso ARPA/Protezione Civile, ma NON confermato da documentazione
    ufficiale — da validare con l'Agenzia regionale prima di un uso
    operativo che confronti questo valore con soglie assolute).
    """
    fetch_at = fetch_at or datetime.now(timezone.utc).replace(tzinfo=None)
    righe = []
    for bollettino_id, bollettino in (payload or {}).items():
        if not isinstance(bollettino, dict):
            continue
        for bacino in bollettino.get("bacino") or []:
            if not isinstance(bacino, dict):
                continue
            nome_bacino = bacino.get("nome_bacino")
            for sotto in bacino.get("sottobacino") or []:
                if not isinstance(sotto, dict):
                    continue
                nome_sottobacino = sotto.get("nomeSottobacino")
                if not nome_sottobacino:
                    continue
                oss = sotto.get("osservazione") or {}
                colmo = sotto.get("colmo_previsto") or {}

                def _val(v):
                    try:
                        return float(v) if v not in (None, "") else None
                    except (ValueError, TypeError):
                        return None

                righe.append({
                    "bollettino_id": str(bollettino_id),
                    "nome_bacino": nome_bacino,
                    "nome_sottobacino": nome_sottobacino,
                    "livello_osservato": _val(oss.get("valore")),
                    "ora_osservazione_raw": oss.get("dataOra"),
                    "livello_colmo_previsto": _val(colmo.get("valore")),
                    "ora_colmo_previsto_raw": colmo.get("dataOra"),
                    "inizio_monitoraggio_raw": bollettino.get("inizioMonitoraggio"),
                    "fine_monitoraggio_raw": bollettino.get("fineMonitoraggio"),
                    "nota_bollettino": bollettino.get("note"),
                    "fetch_at": fetch_at,
                    "fonte": "Allerta Meteo Emilia-Romagna — bollettino di monitoraggio idrologico",
                })
    return righe
