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

import math
import os

from app.attenuation import haversine_km, stima_intensita_pga

# Elenco embedded di comuni di riferimento nell'area storicamente più
# esposta della provincia di Ferrara e zone limitrofe dell'Emilia-Romagna
# (bacino Po/pianura padana, area della sequenza sismica 2012). Coordinate
# reali (centro comune), non generate. Estendibile: passare `comuni`
# esplicitamente alla funzione per coprire un'altra area senza toccare il
# codice.
COMUNI_RIFERIMENTO_FERRARESE = [
    {"nome": "Ferrara", "lat": 44.8381, "lon": 11.6198},
    {"nome": "Cento", "lat": 44.7286, "lon": 11.2848},
    {"nome": "Bondeno", "lat": 44.8908, "lon": 11.4148},
    {"nome": "Mirabello", "lat": 44.8244, "lon": 11.4064},
    {"nome": "Sant'Agostino", "lat": 44.7947, "lon": 11.3667},
    {"nome": "Finale Emilia", "lat": 44.8331, "lon": 11.2969},  # provincia di Modena, stessa sequenza 2012
    {"nome": "Mirandola", "lat": 44.8879, "lon": 11.0664},  # provincia di Modena, stessa sequenza 2012
    {"nome": "Comacchio", "lat": 44.6953, "lon": 12.1836},
    {"nome": "Codigoro", "lat": 44.8306, "lon": 12.1114},
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


def stima_impatto_su_comuni(magnitudo: float, lat_epicentro: float, lon_epicentro: float, profondita_km: float | None, comuni: list | None = None) -> list:
    """
    Per ogni comune di riferimento, calcola distanza dall'epicentro, PGA
    stimata (via il modello di attenuazione già validato-da-validare in
    attenuation.py) ed etichetta qualitativa — ordinato dal più esposto al
    meno esposto, pronto per guidare le prime ispezioni.
    """
    comuni = comuni or COMUNI_RIFERIMENTO_FERRARESE
    risultati = []
    for comune in comuni:
        distanza_km = haversine_km(lat_epicentro, lon_epicentro, comune["lat"], comune["lon"])
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
