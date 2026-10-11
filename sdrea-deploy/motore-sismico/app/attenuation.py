"""
B.2/B.1 — Modello di attenuazione sismica: stima l'intensità di
scuotimento (PGA, accelerazione di picco al suolo, in g) a una data
distanza da un epicentro di data magnitudo.

*** DA VALIDARE CON IL DIPARTIMENTO DI INGEGNERIA DI FERRARA ***
La spec (B.1) è esplicita: "i parametri del modello di attenuazione
sismica... NON vanno decisi internamente". Questo modulo implementa una
forma funzionale standard in sismologia dell'ingegneria (la stessa
famiglia usata da leggi di attenuazione empiriche come Sabetta & Pugliese
1996, calibrata storicamente su eventi italiani) — la FORMA è quindi una
scelta ragionevole e comune, non arbitraria, ma i COEFFICIENTI concreti
(a, b, c, h) sotto sono segnaposto e vanno sostituiti con valori validati
dal Dipartimento prima di un uso in produzione. Sono esternalizzati via
env var proprio per questo: il codice non va riscritto quando arriveranno
i valori giusti, vanno solo impostate le variabili d'ambiente (o un file
di configurazione equivalente).

Forma funzionale (log10 PGA in funzione di magnitudo e distanza ipocentrale):

    log10(PGA) = a + b*M - c*log10(sqrt(R^2 + h^2))

dove:
    M  = magnitudo dell'evento
    R  = distanza epicentrale in km
    h  = profondità ipocentrale "efficace" (evita singolarità a R=0)
    a, b, c = coefficienti del modello di attenuazione (DA VALIDARE)
"""

import math
import os

# --- DA VALIDARE — segnaposto, non usare in produzione senza conferma del
# Dipartimento di Ingegneria (vedi B.1 della spec). Ordini di grandezza
# plausibili per una legge di attenuazione stile Sabetta-Pugliese in
# territorio italiano, ma NON sono stati validati per questo progetto.
ATTENUATION_A = float(os.environ.get("SDREA_ATTENUATION_A", "-1.845"))  # DA VALIDARE
ATTENUATION_B = float(os.environ.get("SDREA_ATTENUATION_B", "0.363"))  # DA VALIDARE
ATTENUATION_C = float(os.environ.get("SDREA_ATTENUATION_C", "1.0"))  # DA VALIDARE
ATTENUATION_H_KM = float(os.environ.get("SDREA_ATTENUATION_H_KM", "10.0"))  # DA VALIDARE — profondità efficace minima


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distanza in km sulla superficie terrestre (formula dell'emisenoverso)."""
    R_TERRA_KM = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R_TERRA_KM * math.asin(math.sqrt(a))


def stima_intensita_pga(magnitudo: float, distanza_epicentrale_km: float, profondita_km: float | None) -> float:
    """
    Ritorna la PGA stimata in g (accelerazione di gravità), sempre >= 0.
    Non lancia mai eccezioni su input fisicamente validi: una distanza 0 e
    profondità 0 sono gestite tramite ATTENUATION_H_KM come termine di
    smorzamento, per evitare log(0) / divisione per zero.
    """
    h = max(profondita_km or 0.0, ATTENUATION_H_KM)
    r_ipocentrale = math.sqrt(distanza_epicentrale_km ** 2 + h ** 2)
    log_pga = ATTENUATION_A + ATTENUATION_B * magnitudo - ATTENUATION_C * math.log10(r_ipocentrale)
    pga = 10 ** log_pga
    return max(pga, 0.0)
