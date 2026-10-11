"""
Motore di priorità combinata idraulico — stessa forma del motore sismico
(app/priority.py in sdrea-motore-sismico/): uno score continuo additivo,
tagliato in 4 fasce da soglie configurabili, con `fattori` sempre
restituiti per intero per la spiegabilità di Vicky.

*** DA VALIDARE CON IL DIPARTIMENTO DI INGEGNERIA / UN IDRAULICO ***
Pesi e soglie sotto sono segnaposto, esternalizzati via env var esattamente
come nel motore sismico — non sono un giudizio operativo, servono solo a
rendere il motore eseguibile ed esplorabile subito.
"""

import os

PESO_ZONA_PGRA_P3 = float(os.environ.get("SDREA_IDRO_PESO_PGRA_P3", "0.30"))  # DA VALIDARE — elevata probabilità di alluvione
PESO_ZONA_PGRA_P2 = float(os.environ.get("SDREA_IDRO_PESO_PGRA_P2", "0.15"))  # DA VALIDARE — media probabilità
PESO_NO_ARGINE = float(os.environ.get("SDREA_IDRO_PESO_NO_ARGINE", "0.20"))  # DA VALIDARE
PESO_RISCHIO_DIRETTO_QUOTA = float(os.environ.get("SDREA_IDRO_PESO_RISCHIO_QUOTA", "0.25"))  # DA VALIDARE — struttura sotto il livello atteso
PESO_EDIFICI_SENSIBILI = float(os.environ.get("SDREA_IDRO_PESO_EDIFICI_SENSIBILI", "0.15"))  # DA VALIDARE
PESO_NO_PERCORSI_ALTERNATIVI = float(os.environ.get("SDREA_IDRO_PESO_NO_PERCORSI_ALT", "0.10"))  # DA VALIDARE
PESO_READINESS_ROSSO = float(os.environ.get("SDREA_IDRO_PESO_READINESS_ROSSO", "0.25"))  # DA VALIDARE
PESO_READINESS_GIALLO = float(os.environ.get("SDREA_IDRO_PESO_READINESS_GIALLO", "0.10"))  # DA VALIDARE

SOGLIA_MEDIA = float(os.environ.get("SDREA_IDRO_SOGLIA_MEDIA", "0.25"))  # DA VALIDARE
SOGLIA_ALTA = float(os.environ.get("SDREA_IDRO_SOGLIA_ALTA", "0.45"))  # DA VALIDARE
SOGLIA_CRITICA = float(os.environ.get("SDREA_IDRO_SOGLIA_CRITICA", "0.70"))  # DA VALIDARE

PRIORITA_RANK = {"bassa": 0, "media": 1, "alta": 2, "critica": 3}


def calcola_priorita(
    superamento_normalizzato: float,
    rischio_diretto_struttura_per_quota: bool | None,
    zona_pgra: str | None,
    presenza_argine_protezione: bool | None,
    readiness_colore: str | None,
    edifici_sensibili_vicini: int | None,
    percorsi_alternativi: bool | None,
    incertezza_corso_acqua: bool = False,
) -> dict:
    """
    Ritorna {"score", "priorita_finale", "priorita_rank", "fattori"} —
    stessa struttura del motore sismico. `incertezza_corso_acqua` (True se
    la struttura non ha `corso_acqua_riferimento` compilato, quindi non è
    stato possibile confermare che sia sullo stesso corso d'acqua della
    stazione — vedi app/idrometria.py) viene riportato nei fattori così
    Vicky può segnalarlo esplicitamente all'operatore invece di presentare
    una priorità con falsa sicurezza.
    """
    fattori = {}

    score = min(superamento_normalizzato, 1.5)
    fattori["contributo_superamento_soglia"] = round(score, 4)

    if readiness_colore == "rosso":
        fattori["contributo_readiness"] = PESO_READINESS_ROSSO
        score += PESO_READINESS_ROSSO
    elif readiness_colore == "giallo":
        fattori["contributo_readiness"] = PESO_READINESS_GIALLO
        score += PESO_READINESS_GIALLO
    else:
        fattori["contributo_readiness"] = 0.0

    if zona_pgra == "P3":
        fattori["contributo_zona_pgra"] = PESO_ZONA_PGRA_P3
        score += PESO_ZONA_PGRA_P3
    elif zona_pgra == "P2":
        fattori["contributo_zona_pgra"] = PESO_ZONA_PGRA_P2
        score += PESO_ZONA_PGRA_P2
    else:
        fattori["contributo_zona_pgra"] = 0.0

    if presenza_argine_protezione is False:
        fattori["contributo_no_argine"] = PESO_NO_ARGINE
        score += PESO_NO_ARGINE
    else:
        fattori["contributo_no_argine"] = 0.0

    if rischio_diretto_struttura_per_quota:
        fattori["contributo_rischio_diretto_quota"] = PESO_RISCHIO_DIRETTO_QUOTA
        score += PESO_RISCHIO_DIRETTO_QUOTA
    else:
        fattori["contributo_rischio_diretto_quota"] = 0.0

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
    fattori["incertezza_corso_acqua"] = incertezza_corso_acqua
    fattori["soglie_usate"] = {
        "soglia_media": SOGLIA_MEDIA, "soglia_alta": SOGLIA_ALTA, "soglia_critica": SOGLIA_CRITICA,
        "nota": "DA VALIDARE con il Dipartimento di Ingegneria/un idraulico — vedi app/priority.py",
    }

    return {
        "score": round(score, 4),
        "priorita_finale": priorita,
        "priorita_rank": PRIORITA_RANK[priorita],
        "fattori": fattori,
    }
