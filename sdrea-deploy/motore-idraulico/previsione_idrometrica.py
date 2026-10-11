"""
Modulo di previsione idrometrica a brevissimo termine — motore idraulico.

*** PRINCIPIO NON NEGOZIABILE DI QUESTO MODULO, letto per intero prima di
usarlo o modificarlo: NON esiste qui un modello idrologico del fiume Po. ***

Il livello del Po a Pontelagoscuro (Ferrara) è determinato dalle piogge
cadute anche giorni prima su un bacino di oltre 70.000 kmq che comprende
gran parte delle Alpi e dell'Appennino settentrionale — un fenomeno che si
propaga con giorni di ritardo (tempo di corrivazione) rispetto alla pioggia
locale su Ferrara. Un modulo che stimasse "il Po salirà perché piove su
Ferrara" darebbe un'informazione falsa e pericolosa in un sistema di
protezione civile: è esattamente il tipo di errore che va evitato.

Per questo il modulo tiene rigorosamente separati due segnali, che l'API
restituisce come due chiavi distinte e MAI combinate in un unico numero:

1. `trend_livello_fiume` — un'estrapolazione statistica di brevissimo
   respiro (poche osservazioni successive) delle letture GIA' UFFICIALI
   del bollettino di monitoraggio idrologico di Allerta Meteo
   Emilia-Romagna per il sottobacino richiesto. Non inventa fisica: prende
   le osservazioni reali già ingerite (vedi allerta_meteo_client.py) e
   calcola una semplice regressione lineare per stimare la prossima
   osservazione, dichiarando sempre quante osservazioni ha usato e con
   quale margine di incertezza. Se il bollettino ufficiale fornisce già un
   "colmo previsto", quello resta SEMPRE la previsione autorevole: questo
   trend è un'indicazione complementare a brevissimo termine tra un
   bollettino e l'altro, non un sostituto.

2. `rischio_pluviale_locale` — un indicatore, su base Open-Meteo (dati
   meteorologici pubblici reali, non a pagamento), del rischio di
   allagamento urbano/pluviale LOCALE (acqua piovana che non defluisce in
   città), fenomeno fisicamente diverso e indipendente dal livello del Po.
   Anche qui: soglie euristiche dichiarate, non un modello idraulico urbano
   validato — DA VALIDARE con il Comune/Consorzio di Bonifica competente
   prima di un uso operativo.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

OPEN_METEO_BASE_URL = os.environ.get("OPEN_METEO_BASE_URL", "https://api.open-meteo.com")
DEFAULT_TIMEOUT_S = 10.0

# Coordinate di riferimento per il rischio pluviale locale: centro di
# Ferrara. Configurabile per estendere ad altre città senza toccare il
# codice (vedi richiesta esplicita del fondatore di dare priorità a
# Ferrara restando comunque estendibili ad altre città della regione).
FERRARA_LAT = float(os.environ.get("SDREA_FERRARA_LAT", "44.8381"))
FERRARA_LON = float(os.environ.get("SDREA_FERRARA_LON", "11.6198"))

# Soglie euristiche per il rischio pluviale locale — DA VALIDARE con il
# Comune di Ferrara / Consorzio di Bonifica Pianura di Ferrara: valori
# plausibili di buon senso ingegneristico (non arbitrari: 20mm/24h è la
# soglia comunemente citata come "pioggia forte" nelle scale SIR-ARPAE),
# ma non calibrati su questa specifica rete fognaria/di bonifica.
SOGLIA_PIOGGIA_MODERATA_MM = float(os.environ.get("SDREA_SOGLIA_PIOGGIA_MODERATA_MM", "20.0"))  # DA VALIDARE
SOGLIA_PIOGGIA_ELEVATA_MM = float(os.environ.get("SDREA_SOGLIA_PIOGGIA_ELEVATA_MM", "50.0"))  # DA VALIDARE


def estrai_serie_storica(db, LetturaBollettinoMonitoraggio, nome_sottobacino: str, n: int = 6) -> list:
    """
    Ultime `n` osservazioni realmente distinte (per costruzione: il poll è
    idempotente su ora_osservazione_raw, quindi ogni riga qui è
    un'osservazione ufficiale diversa, non un duplicato di poll) per un
    dato sottobacino, in ordine cronologico crescente (dalla più vecchia
    alla più recente) pronte per la regressione.
    """
    righe = (
        db.query(LetturaBollettinoMonitoraggio)
        .filter(LetturaBollettinoMonitoraggio.nome_sottobacino == nome_sottobacino)
        .filter(LetturaBollettinoMonitoraggio.livello_osservato.isnot(None))
        .order_by(LetturaBollettinoMonitoraggio.id.desc())
        .limit(n)
        .all()
    )
    return list(reversed(righe))  # dalla più vecchia alla più recente


def stima_trend_livello(letture: list) -> dict:
    """
    Regressione lineare (minimi quadrati) del livello osservato rispetto
    all'indice della lettura (0, 1, 2, ...) — NON rispetto al tempo reale,
    perché l'intervallo tra due bollettini successivi non è garantito
    costante: dichiariamo onestamente "prossima osservazione", non
    "tra N ore", a meno che l'intervallo osservato sia effettivamente
    regolare (vedi `intervallo_medio_h` sotto, calcolato dai fetch_at reali
    quando disponibili).
    """
    n = len(letture)
    if n == 0:
        return {
            "disponibile": False,
            "motivo": "Nessuna osservazione ufficiale trovata per questo sottobacino.",
        }
    if n == 1:
        return {
            "disponibile": True,
            "n_osservazioni_usate": 1,
            "livello_osservato_ultimo": letture[0].livello_osservato,
            "tendenza": "indeterminata",
            "livello_stimato_prossima_osservazione": None,
            "margine_incertezza_m": None,
            "nota": "Una sola osservazione disponibile: non è possibile calcolare un trend. "
                    "Servono almeno 2-3 osservazioni successive dello stesso sottobacino.",
        }

    valori = [l.livello_osservato for l in letture]
    xs = list(range(n))
    x_media = sum(xs) / n
    y_media = sum(valori) / n
    num = sum((xs[i] - x_media) * (valori[i] - y_media) for i in range(n))
    den = sum((xs[i] - x_media) ** 2 for i in range(n))
    pendenza = num / den if den else 0.0
    intercetta = y_media - pendenza * x_media

    # Residui, per un margine di incertezza onesto (non un intervallo di
    # confidenza statisticamente rigoroso con n così piccolo, ma una banda
    # basata sulla dispersione osservata invece di un numero inventato).
    residui = [valori[i] - (intercetta + pendenza * xs[i]) for i in range(n)]
    scarto_quadratico_medio = (sum(r ** 2 for r in residui) / n) ** 0.5
    margine = max(scarto_quadratico_medio * 1.5, 0.05)  # almeno 5cm, mai un falso "zero incertezza"

    prossimo_valore = intercetta + pendenza * n

    if abs(pendenza) < 0.02:
        tendenza = "stabile"
    elif pendenza > 0:
        tendenza = "in crescita"
    else:
        tendenza = "in calo"

    # Intervallo medio reale tra osservazioni, se i fetch_at sono
    # disponibili — usato solo per dare un'idea di quanto sia "fresco" il
    # trend, MAI per proiettare un orario preciso.
    intervallo_medio_h = None
    fetch_ats = [l.fetch_at for l in letture if l.fetch_at]
    if len(fetch_ats) >= 2:
        delta_totale_h = (fetch_ats[-1] - fetch_ats[0]).total_seconds() / 3600.0
        intervallo_medio_h = round(delta_totale_h / (len(fetch_ats) - 1), 1) if len(fetch_ats) > 1 else None

    return {
        "disponibile": True,
        "n_osservazioni_usate": n,
        "livello_osservato_ultimo": valori[-1],
        "tendenza": tendenza,
        "pendenza_m_per_osservazione": round(pendenza, 3),
        "livello_stimato_prossima_osservazione": round(prossimo_valore, 2),
        "margine_incertezza_m": round(margine, 2),
        "intervallo_medio_tra_osservazioni_h": intervallo_medio_h,
        "nota": (
            "Estrapolazione statistica lineare sulle ultime osservazioni ufficiali del "
            "bollettino di monitoraggio idrologico regionale. Non è un modello idraulico del "
            "bacino del Po: se il bollettino ufficiale indica un colmo di piena previsto, "
            "quel valore è sempre la previsione autorevole da usare per le decisioni operative."
        ),
    }


def fetch_previsione_pioggia(lat: float, lon: float) -> dict:
    """
    Interroga Open-Meteo (api.open-meteo.com/v1/forecast) — API pubblica,
    gratuita, senza chiave, CORS-abilitata — per la pioggia prevista nelle
    prossime 24 ore nel punto indicato. Stessa fonte già usata lato client
    in SDREA.html per il modulo Sorveglianza Ambientale; qui viene
    interrogata anche lato server per alimentare Vicky e il pannello
    idraulico senza dipendere dal browser dell'operatore.
    """
    url = (
        f"{OPEN_METEO_BASE_URL}/v1/forecast?latitude={lat}&longitude={lon}"
        "&hourly=precipitation,precipitation_probability&forecast_days=2&timezone=Europe%2FRome"
    )
    resp = httpx.get(url, timeout=DEFAULT_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


def valuta_rischio_pluviale_locale(payload: dict) -> dict:
    """
    Classifica il rischio pluviale locale (allagamento urbano, NON livello
    del Po) sulla base della pioggia cumulata prevista nelle prossime 24h
    e della probabilità massima oraria, secondo soglie euristiche
    dichiarate (vedi SOGLIA_PIOGGIA_* sopra, DA VALIDARE).
    """
    orario = payload.get("hourly") or {}
    precipitazioni = orario.get("precipitation") or []
    probabilita = orario.get("precipitation_probability") or []

    prossime_24h_precip = precipitazioni[:24]
    prossime_24h_prob = probabilita[:24]

    cumulata_mm = round(sum(v for v in prossime_24h_precip if isinstance(v, (int, float))), 1)
    probabilita_massima_pct = max(prossime_24h_prob) if prossime_24h_prob else None

    if cumulata_mm >= SOGLIA_PIOGGIA_ELEVATA_MM:
        livello = "elevato"
    elif cumulata_mm >= SOGLIA_PIOGGIA_MODERATA_MM:
        livello = "moderato"
    else:
        livello = "basso"

    return {
        "livello_rischio_pluviale_locale": livello,
        "pioggia_cumulata_prevista_24h_mm": cumulata_mm,
        "probabilita_massima_pioggia_pct": probabilita_massima_pct,
        "soglie_usate_mm": {"moderato": SOGLIA_PIOGGIA_MODERATA_MM, "elevato": SOGLIA_PIOGGIA_ELEVATA_MM},
        "fonte": "Open-Meteo (api.open-meteo.com) — previsione meteorologica pubblica",
        "nota": (
            "Indicatore di rischio pluviale/urbano locale (acqua piovana che non defluisce in "
            "città), fisicamente distinto dal livello del fiume Po: non usare questo valore come "
            "proxy del rischio di piena fluviale. Soglie euristiche da validare con il gestore "
            "locale della rete fognaria/di bonifica prima di un uso operativo."
        ),
    }
