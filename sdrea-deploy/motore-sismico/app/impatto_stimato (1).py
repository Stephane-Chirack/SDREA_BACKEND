"""
Risposta rapida post-evento — il vero valore aggiunto realistico del
motore sismico, distinto e più onesto della "previsione sismica" (che non
esiste scientificamente: nessun sistema al mondo prevede quando/dove
avverrà un terremoto, e SDREA non lo promette).

Due funzioni, entrambe standard in sismologia dell'ingegneria, non
inventate per questo progetto:

1. `stima_impatto_su_comuni` — usa il modello di attenuazione già presente
   in app/attenuation.py (Sabetta-Pugliese, coefficienti DA VALIDARE come
   già dichiarato in quel file) per stimare, appena arriva un evento reale
   da INGV, l'intensità di scuotimento attesa (PGA) in un elenco di comuni
   vicini — stessa logica di principio di ShakeMap (USGS/INGV), utile per
   dare priorità immediata alle ispezioni nei primi minuti dopo un evento,
   quando non c'è ancora nessun sopralluogo.

2. `stima_probabilita_repliche` — legge di Omori-Utsu, formula empirica
   consolidata (dal 1894, raffinata da Utsu nel 1961) per il decadimento
   nel tempo del tasso di scosse di assestamento dopo un evento principale.
   Anche qui i parametri (K, c, p) sono tipici della letteratura sismologica
   italiana ma NON calibrati sulla sismicità specifica di questa zona — DA
   VALIDARE con INGV/Dipartimento prima di un uso operativo che vada oltre
   l'indicazione di massima.
"""

from __future__ import annotations

import json
import math
import os

from app.attenuation import haversine_km, stima_intensita_pga

# RAGGIO_MASSIMO_KM: se l'epicentro è più lontano di così da OGNI comune
# del catalogo sotto, la lista "comuni più esposti" resta vuota invece di
# mostrare comunque i comuni più vicini con una PGA trascurabile — un
# evento a centinaia di km di distanza non ha comuni "esposti" pertinenti
# nel catalogo, punto (bug segnalato dall'utente e corretto qui). Con la
# copertura ora estesa a tutta Italia (vedi sotto), 150km resta un raggio
# ragionevole: qualunque punto del territorio nazionale ha quasi sempre un
# comune del catalogo nel raggio di poche decine di km (anche nelle zone
# montane meno dense).
RAGGIO_MASSIMO_KM = 150.0
# Catalogo nazionale dei capoluoghi comunali italiani (coordinate reali,
# centroide della sede municipale, standard EPSG:4326) — esteso a tutta
# Italia il 2026-10-06 (prima copriva solo i 330 comuni dell'Emilia-Romagna,
# già un ampliamento rispetto ai 9 iniziali dell'area ferrarese): per
# mostrare il reale potenziale nazionale di SDREA, non solo quello legato
# alla candidatura regionale Great-ER 2.0, e perché un evento sismico reale
# può verificarsi ovunque in Italia durante una dimostrazione dal vivo,
# mentre un evento alluvionale reale nella finestra di una demo è molto più
# improbabile (limite dichiarato del motore idraulico, non risolvibile allo
# stesso modo: dipende da un bollettino regionale, non da un catalogo di
# punti geografici).
#
# FONTE: dataset pubblico "comuni-italiani" (opendatasicilia su GitHub),
# file dati/main.csv, scaricato e verificato il 2026-10-06 — 7904 comuni
# italiani risultanti (coerente con il numero ufficiale ISTAT). 11 valori
# avevano un punto decimale mancante nella fonte (stesso tipo di difetto già
# riscontrato per Montegridolfo nell'elenco precedente, es. "45631" invece
# di "45.631"): corretti qui dividendo per la potenza di 10 che riporta la
# coordinata entro i confini geografici italiani plausibili (lat 35–47.5,
# lon 6–19), verificati singolarmente per coerenza con la provincia
# dichiarata — NON confermati da una seconda fonte indipendente: da
# ricontrollare se uno di questi comuni specifici diventa rilevante in un
# caso reale.
#
# Caricato da app/data/comuni_italia.json invece di restare un letterale
# Python inline (come il precedente elenco regionale) solo per leggibilità
# del codice con ~7900 righe di dati — stesso contenuto, stesso principio:
# se il file manca o non carica, l'app fallisce esplicitamente all'avvio
# (nessun catalogo vuoto o parziale servito in silenzio).
#
# Resta un limite dichiarato: questo è un catalogo dei CAPOLUOGHI comunali
# (un punto per comune), non delle infrastrutture specifiche (ponti, dighe,
# scuole, ospedali) — per quello esiste già il progetto collegato
# "Ponti e Daghi" (strutture puntuali valutate separatamente). Qui serve
# solo a dare priorità di ispezione per area quando arriva un evento reale.
_COMUNI_ITALIA_PATH = os.path.join(os.path.dirname(__file__), "data", "comuni_italia.json")
with open(_COMUNI_ITALIA_PATH, encoding="utf-8") as _f:
    COMUNI_ITALIA = [
        {"nome": c["nome"], "lat": c["lat"], "lon": c["lon"]}
        for c in json.load(_f)
    ]

# Scala qualitativa di intensità percepita da PGA (in g) — soglie
# indicative comunemente citate in letteratura per la scala Mercalli
# modificata approssimata, NON un rigore scientifico da bollettino
# ufficiale: usata solo per dare un'etichetta leggibile a un operatore,
# mai come sostituto di un rilievo macrosismico reale (quello lo fa solo
# l'INGV dopo l'evento).
_SOGLIE_INTENSITA_PGA_G = [
    (0.005, "non avvertito"),
    (0.02, "avvertito lievemente"),
    (0.05, "avvertito, oggetti oscillano"),
    (0.10, "avvertito diffusamente, possibili danni lievi a strutture vulnerabili"),
    (0.20, "danni moderati possibili"),
    (0.35, "danni significativi possibili"),
]


def _etichetta_intensita(pga_g: float) -> str:
    etichetta = "danni gravi possibili"
    for soglia, nome in _SOGLIE_INTENSITA_PGA_G:
        if pga_g < soglia:
            etichetta = nome
            break
    return etichetta


def stima_impatto_su_comuni(magnitudo: float, lat_epicentro: float, lon_epicentro: float, profondita_km: float | None, comuni: list | None = None, raggio_massimo_km: float = RAGGIO_MASSIMO_KM) -> list:
    """
    Per ogni comune di riferimento ENTRO raggio_massimo_km dall'epicentro,
    calcola distanza, PGA stimata (via il modello di attenuazione già
    validato-da-validare in attenuation.py) ed etichetta qualitativa —
    ordinato dal più esposto al meno esposto, pronto per guidare le prime
    ispezioni. Un comune oltre raggio_massimo_km viene escluso invece di
    essere mostrato come "esposto" con una PGA trascurabile: il catalogo
    copre tutti i comuni italiani (vedi nota sopra), quindi una lista vuota
    vuol dire che l'epicentro è fuori dai confini nazionali o in mare aperto
    oltre la distanza utile — mai un dato fuorviante per riempirla comunque.
    """
    comuni = comuni or COMUNI_ITALIA
    risultati = []
    for comune in comuni:
        distanza_km = haversine_km(lat_epicentro, lon_epicentro, comune["lat"], comune["lon"])
        if distanza_km > raggio_massimo_km:
            continue
        pga_g = stima_intensita_pga(magnitudo, distanza_km, profondita_km)
        risultati.append({
            "comune": comune["nome"],
            "distanza_epicentro_km": round(distanza_km, 1),
            "pga_stimata_g": round(pga_g, 4),
            "intensita_percepita_stimata": _etichetta_intensita(pga_g),
        })
    risultati.sort(key=lambda r: r["pga_stimata_g"], reverse=True)
    return risultati


# --- Legge di Omori-Utsu — parametri DA VALIDARE (vedi docstring modulo) ---
OMORI_K = float(os.environ.get("SDREA_OMORI_K", "1.0"))  # DA VALIDARE — produttività della sequenza
OMORI_C = float(os.environ.get("SDREA_OMORI_C_GIORNI", "0.05"))  # DA VALIDARE — giorni, evita singolarità a t=0
OMORI_P = float(os.environ.get("SDREA_OMORI_P", "1.05"))  # DA VALIDARE — tipicamente vicino a 1


def stima_probabilita_repliche(magnitudo_evento_principale: float, ore_trascorse: float, finestra_ore: float = 24.0) -> dict:
    """
    Tasso atteso di repliche nella finestra [ore_trascorse, ore_trascorse+finestra_ore]
    secondo Omori-Utsu: n(t) = K / (t + c)^p (t in giorni).
    Integra numericamente su un passo fine invece di usare la primitiva
    analitica esatta (identica per p≈1 vs p=1: evitare un ramo di codice
    speciale per p=1 in cambio di un errore numerico trascurabile a questa
    risoluzione).

    Ritorna il numero atteso di repliche di magnitudo pari o superiore a
    (magnitudo_evento_principale - 1.0) — soglia standard "M-1" usata in
    letteratura per le repliche più significative — nella finestra data.
    Il numero atteso, non una probabilità 0-1 diretta, viene poi convertito
    in probabilità di ALMENO UNA replica assumendo un processo di Poisson
    (approssimazione standard per sequenze sismiche).
    """
    t0_giorni = ore_trascorse / 24.0
    t1_giorni = (ore_trascorse + finestra_ore) / 24.0
    passi = 200
    dt = (t1_giorni - t0_giorni) / passi
    numero_atteso = 0.0
    for i in range(passi):
        t = t0_giorni + (i + 0.5) * dt
        numero_atteso += (OMORI_K / ((t + OMORI_C) ** OMORI_P)) * dt

    probabilita_almeno_una = 1 - math.exp(-numero_atteso)

    return {
        "magnitudo_soglia_replica": round(magnitudo_evento_principale - 1.0, 1),
        "finestra_ore": finestra_ore,
        "numero_atteso_repliche": round(numero_atteso, 3),
        "probabilita_almeno_una_replica_pct": round(probabilita_almeno_una * 100, 1),
        "modello": "Omori-Utsu (n(t) = K / (t + c)^p), parametri generici di letteratura — DA VALIDARE su questa sequenza specifica",
        "nota": (
            "Questa è una stima statistica generica del decadimento delle repliche nel tempo, "
            "non una previsione di un evento specifico: nessun modello scientifico può prevedere "
            "se, dove o quando avverrà una singola replica. Utile solo come indicazione di massima "
            "sul livello di allerta operativa nelle ore successive a un evento principale."
        ),
    }
