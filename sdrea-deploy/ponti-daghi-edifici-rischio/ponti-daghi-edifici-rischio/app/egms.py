"""
A.7 — Integrazione EGMS (Copernicus European Ground Motion Service).

Nota tecnica sull'accesso ai dati EGMS (letta prima di implementare, non
assunta): EGMS non espone un endpoint REST pubblico "dammi lat/lon, ricevi
JSON" per un singolo punto. I dati (prodotti Basic/Calibrated/Ortho,
2016-oggi, un valore ogni ~6-12 giorni riaggregato tipicamente su base
annuale nell'Explorer) sono distribuiti come servizi OGC (WFS/WMS) via il
catalogo geospaziale EEA/Copernicus Land Monitoring Service, oppure
scaricabili come CSV/vettoriali dal portale EGMS Explorer per un'area di
interesse. Wiring una dipendenza di produzione contro un endpoint WFS
specifico (URL, parametri di query, eventuale autenticazione) senza aver
verificato quell'endpoint end-to-end sarebbe lo stesso errore che la spec
chiede di evitare altrove (B.1-B.3): un valore "inventato" in fase di
sviluppo invece che validato.

Per questo l'integrazione qui è a due livelli, entrambi reali (non stub):

1. Percorso primario, funzionante da subito: IMPORT dei dati scaricati
   dall'EGMS Explorer (CSV/JSON) per una struttura — esattamente il
   workflow che un tecnico userebbe oggi in pratica ("Explorer" è
   esplicitamente citato in spec A.7 come canale valido, non solo "API").
   Vedi POST /api/structures/{id}/egms/import in app/main.py.

2. Percorso opzionale, per quando l'ingegnere avrà confermato endpoint e
   modalità di accesso concordati col Dipartimento: EGMS_WFS_BASE_URL è
   esternalizzato come parametro di configurazione (vuoto di default —
   nessun URL indovinato), pronto per essere valorizzato senza toccare il
   codice.

Le SOGLIE di allarme sul trend sono anch'esse esternalizzate e NON decise
qui — vedi EGMS_VELOCITA_ATTENZIONE_MM_ANNO e
EGMS_ACCELERAZIONE_SOGLIA_MM_ANNO2 più sotto: i valori di default sono
segnaposto ragionevoli per non rompere il calcolo, ma vanno validati con un
ingegnere strutturista prima di un uso in produzione (vedi nota finale
della spec).
"""

import os

# --- DA VALIDARE con un ingegnere strutturista / il Dipartimento di
# Ingegneria prima dell'uso in produzione (vedi nota finale della spec,
# che cita esplicitamente A.7 tra i parametri da non fissare
# autonomamente in fase di sviluppo). Esternalizzati via env var così il
# codice è pronto ma il valore non è scelto qui.
EGMS_VELOCITA_ATTENZIONE_MM_ANNO = float(
    os.environ.get("EGMS_VELOCITA_ATTENZIONE_MM_ANNO", "5.0")
)  # DA VALIDARE — placeholder
EGMS_ACCELERAZIONE_SOGLIA_MM_ANNO2 = float(
    os.environ.get("EGMS_ACCELERAZIONE_SOGLIA_MM_ANNO2", "2.0")
)  # DA VALIDARE — placeholder

# Percorso 2 (opzionale, vuoto finché non confermato — vedi docstring sopra).
EGMS_WFS_BASE_URL = os.environ.get("EGMS_WFS_BASE_URL", "").strip()

TREND_LABELS = {
    "insufficiente": "dati insufficienti",
    "stabile": "stabile",
    "significativo": "movimento significativo (stabile)",
    "accelerazione": "in accelerazione",
}


def _linreg_slope(xs: list[float], ys: list[float]) -> float | None:
    """Pendenza di una regressione lineare semplice (minimi quadrati). None se non calcolabile."""
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return None
    return num / den


def compute_trend(points: list[dict]) -> dict:
    """
    points: lista di {"data": date, "spostamento_cumulato_mm": float}, in
    qualunque ordine — vengono ordinati qui per data.

    Calcola la velocità media (mm/anno) sull'intera serie e, se ci sono
    abbastanza punti, confronta la velocità nella seconda metà della serie
    con quella nella prima metà per rilevare un'accelerazione (non solo
    "si sta muovendo", ma "si sta muovendo sempre più in fretta" — il
    segnale che la spec chiede di esporre, coerente con l'obiettivo di
    A.2 di intercettare un deterioramento progressivo, non solo uno stato
    statico).

    Ritorna sempre un dict con almeno {"classificazione", "descrizione"};
    aggiunge velocita_mm_anno / velocita_recente_mm_anno / n_punti quando
    calcolabili. Non lancia mai eccezioni su input scarso: con <3 punti
    risponde "dati insufficienti", esplicitamente, invece di inventare un
    trend da un solo segmento.
    """
    if len(points) < 3:
        return {
            "classificazione": "insufficiente",
            "descrizione": TREND_LABELS["insufficiente"],
            "n_punti": len(points),
        }

    ordered = sorted(points, key=lambda p: p["data"])
    t0 = ordered[0]["data"]
    xs_years = [(p["data"] - t0).days / 365.25 for p in ordered]
    ys = [p["spostamento_cumulato_mm"] for p in ordered]

    slope_overall = _linreg_slope(xs_years, ys)
    if slope_overall is None:
        return {
            "classificazione": "insufficiente",
            "descrizione": TREND_LABELS["insufficiente"],
            "n_punti": len(points),
        }

    mid = len(ordered) // 2
    slope_recent = _linreg_slope(xs_years[mid:], ys[mid:])
    if slope_recent is None:
        slope_recent = slope_overall

    velocita = abs(slope_overall)
    velocita_recente = abs(slope_recent)

    if velocita_recente < EGMS_VELOCITA_ATTENZIONE_MM_ANNO:
        classificazione = "stabile"
    elif (velocita_recente - velocita) >= EGMS_ACCELERAZIONE_SOGLIA_MM_ANNO2:
        classificazione = "accelerazione"
    else:
        classificazione = "significativo"

    return {
        "classificazione": classificazione,
        "descrizione": TREND_LABELS[classificazione],
        "velocita_mm_anno": round(velocita, 2),
        "velocita_recente_mm_anno": round(velocita_recente, 2),
        "n_punti": len(points),
        "soglie_usate": {
            "velocita_attenzione_mm_anno": EGMS_VELOCITA_ATTENZIONE_MM_ANNO,
            "accelerazione_soglia_mm_anno2": EGMS_ACCELERAZIONE_SOGLIA_MM_ANNO2,
            "nota": "DA VALIDARE con un ingegnere strutturista — vedi app/egms.py",
        },
    }
