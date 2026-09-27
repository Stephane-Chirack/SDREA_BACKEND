"""
B.3 — Motore di priorità combinata.

*** DA VALIDARE CON IL DIPARTIMENTO DI INGEGNERIA DI FERRARA ***
Come per l'attenuazione (app/attenuation.py), la spec è esplicita: pesi e
soglie di questa funzione "vanno validati con il Dipartimento, non fissati
arbitrariamente in fase di sviluppo". I fattori considerati sono quelli
elencati testualmente in B.3 della spec — quello che NON è dato dalla spec
sono i pesi relativi e le soglie numeriche, che qui sono segnaposto
esternalizzati (env var), non valori scelti con criterio ingegneristico.

Funzione (form): uno "score" continuo, poi tagliato in 4 fasce
(bassa/media/alta/critica) da soglie configurabili — così la stessa
funzione conserva un valore continuo utile a Vicky (B.4.4, spiegabilità:
"perché critica e non alta") oltre alla sola etichetta finale.
"""

import os

# --- DA VALIDARE — vedi nota sopra. Pesi relativi dei fattori di
# vulnerabilità/esposizione booleani (A.2/A.3) nello score complessivo, e
# soglie che separano le 4 fasce di priorità. Nessuno di questi valori è
# stato validato da un ingegnere strutturista: sono segnaposto che rendono
# il motore eseguibile ed esplorabile, non un giudizio operativo.
PESO_NON_RIDONDANTE = float(os.environ.get("SDREA_PESO_NON_RIDONDANTE", "0.15"))  # DA VALIDARE
PESO_NO_NORMATIVA_ANTISISMICA = float(os.environ.get("SDREA_PESO_NO_NORMATIVA", "0.15"))  # DA VALIDARE
PESO_EDIFICI_SENSIBILI = float(os.environ.get("SDREA_PESO_EDIFICI_SENSIBILI", "0.15"))  # DA VALIDARE
PESO_NO_PERCORSI_ALTERNATIVI = float(os.environ.get("SDREA_PESO_NO_PERCORSI_ALT", "0.10"))  # DA VALIDARE
PESO_READINESS_ROSSO = float(os.environ.get("SDREA_PESO_READINESS_ROSSO", "0.25"))  # DA VALIDARE
PESO_READINESS_GIALLO = float(os.environ.get("SDREA_PESO_READINESS_GIALLO", "0.10"))  # DA VALIDARE

# Soglie sullo score finale [0, ~1.x] che separano le 4 fasce — DA VALIDARE.
SOGLIA_MEDIA = float(os.environ.get("SDREA_SOGLIA_MEDIA", "0.25"))  # DA VALIDARE
SOGLIA_ALTA = float(os.environ.get("SDREA_SOGLIA_ALTA", "0.45"))  # DA VALIDARE
SOGLIA_CRITICA = float(os.environ.get("SDREA_SOGLIA_CRITICA", "0.70"))  # DA VALIDARE

# PGA (g) oltre la quale il fattore "intensità" da solo può già dominare lo
# score — normalizza intensita_stimata a [0,1] circa (0.4g ~ scuotimento
# molto forte). DA VALIDARE.
PGA_NORMALIZZAZIONE = float(os.environ.get("SDREA_PGA_NORMALIZZAZIONE", "0.4"))  # DA VALIDARE

PRIORITA_RANK = {"bassa": 0, "media": 1, "alta": 2, "critica": 3}


def calcola_priorita(
    intensita_stimata_g: float,
    readiness_colore: str | None,
    ridondanza: str | None,
    normativa_antisismica: bool | None,
    edifici_sensibili_vicini: int | None,
    percorsi_alternativi: bool | None,
) -> dict:
    """
    Ritorna {"score", "priorita_finale", "priorita_rank", "fattori"} — mai
    solo l'etichetta: "fattori" è la scomposizione additiva dello score,
    usata direttamente da Vicky (B.4.4) per spiegare "perché" senza dover
    ricalcolare nulla né restituire una risposta a scatola chiusa.
    """
    fattori = {}

    intensita_norm = min(intensita_stimata_g / PGA_NORMALIZZAZIONE, 1.5)
    fattori["intensita_stimata_g"] = round(intensita_stimata_g, 4)
    fattori["contributo_intensita"] = round(intensita_norm, 4)
    score = intensita_norm

    if readiness_colore == "rosso":
        fattori["contributo_readiness"] = PESO_READINESS_ROSSO
        score += PESO_READINESS_ROSSO
    elif readiness_colore == "giallo":
        fattori["contributo_readiness"] = PESO_READINESS_GIALLO
        score += PESO_READINESS_GIALLO
    else:
        fattori["contributo_readiness"] = 0.0

    if ridondanza == "non_ridondante":
        fattori["contributo_non_ridondante"] = PESO_NON_RIDONDANTE
        score += PESO_NON_RIDONDANTE
    else:
        fattori["contributo_non_ridondante"] = 0.0

    if normativa_antisismica is False:
        fattori["contributo_no_normativa_antisismica"] = PESO_NO_NORMATIVA_ANTISISMICA
        score += PESO_NO_NORMATIVA_ANTISISMICA
    else:
        fattori["contributo_no_normativa_antisismica"] = 0.0

    if (edifici_sensibili_vicini or 0) > 0:
        fattori["contributo_edifici_sensibili"] = PESO_EDIFICI_SENSIBILI
        score += PESO_EDIFICI_SENSIBILI
    else:
        fattori["contributo_edifici_sensibili"] = 0.0

    if percorsi_alternativi is False:
        fattori["contributo_no_percorsi_alternativi"] = PESO_NO_PERCORSI_ALTERNATIVI
        score += PESO_NO_PERCORSI_ALTERNATIVI
    else:
        fattori["contributo_no_percorsi_alternativi"] = 0.0

    if score >= SOGLIA_CRITICA:
        priorita = "critica"
    elif score >= SOGLIA_ALTA:
        priorita = "alta"
    elif score >= SOGLIA_MEDIA:
        priorita = "media"
    else:
        priorita = "bassa"

    fattori["score_totale"] = round(score, 4)
    fattori["soglie_usate"] = {
        "soglia_media": SOGLIA_MEDIA, "soglia_alta": SOGLIA_ALTA, "soglia_critica": SOGLIA_CRITICA,
        "nota": "DA VALIDARE con il Dipartimento di Ingegneria — vedi app/priority.py",
    }

    return {
        "score": round(score, 4),
        "priorita_finale": priorita,
        "priorita_rank": PRIORITA_RANK[priorita],
        "fattori": fattori,
    }
