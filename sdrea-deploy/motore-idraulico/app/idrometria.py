"""
Motore di stima dell'impatto idraulico — equivalente idraulico di
sdrea-motore-sismico/app/attenuation.py, ma con una forma funzionale
diversa perché il fenomeno fisico è diverso.

*** DA VALIDARE CON IL DIPARTIMENTO DI INGEGNERIA / UN IDRAULICO ***
Un sisma si attenua in modo approssimativamente radiale da un epicentro:
un modello di attenuazione distanza-magnitudo è scientificamente
appropriato (vedi il motore sismico). Una piena NON si propaga
radialmente da una stazione idrometrica: si propaga lungo la rete
idrografica (il fiume, i suoi affluenti, gli argini), e una struttura a
2 km in linea d'aria dalla stazione ma su un altro bacino può essere
completamente non coinvolta, mentre una struttura a 15 km ma sullo stesso
corso d'acqua a valle può esserlo eccome, con un ritardo temporale che
dipende dalla velocità di propagazione dell'onda di piena.

Questo modulo NON ha accesso a un modello idraulico di propagazione reale
(servirebbe un DEM del bacino, una rete idrografica vettoriale e un
modello di routing idraulico — dati e competenza che vanno oltre lo scope
di questa sessione di sviluppo). Per rendere il motore eseguibile e
testabile subito, usa due proxy dichiarati esplicitamente come
segnaposto:

1. `corso_acqua_riferimento` della struttura (dato ufficiale lato
   Ponti-Daghi-Edifici, mai calcolato) deve corrispondere al
   `corso_d_acqua` della stazione: se non corrisponde, la struttura è
   esclusa dalla valutazione per quell'evento, invece di essere valutata
   con una distanza in linea d'aria che non avrebbe senso idraulico.
2. Per le strutture sullo stesso corso d'acqua, la distanza in linea
   d'aria (haversine) è usata come proxy grezzo di "quanto lontano lungo
   il fiume", esplicitamente segnalato come impreciso: va sostituito da
   un vero modello di routing idraulico (o quantomeno da una distanza
   calcolata lungo la polilinea del corso d'acqua) prima di un uso
   operativo che decida priorità di ispezione in base al tempo di arrivo
   dell'onda di piena.

La stima dell'impatto (quanto un livello idrometrico critico "pesa" su
una struttura) usa invece un principio più solido: quanto il livello
osservato supera le soglie ufficiali della stazione (soglia1/2/3),
esattamente il sistema già in uso da ARPA/Protezione Civile per i colori
di allerta — qui non reinventiamo una scala di intensità come la PGA
sismica, riusiamo quella istituzionale.
"""

import math
import os

# Se la struttura ha una quota (quota_slm_m, lato Ponti-Daghi-Edifici) più
# bassa del livello idrometrico corrente meno questo margine di sicurezza
# (m), consideriamo un rischio di allagamento diretto della struttura
# stessa, non solo di area circostante — DA VALIDARE con un idraulico
# (dipende dalla presenza e quota degli argini, non solo dalla quota del
# terreno).
MARGINE_SICUREZZA_QUOTA_M = float(os.environ.get("SDREA_IDRO_MARGINE_QUOTA_M", "1.5"))  # DA VALIDARE

# Oltre questa distanza (km) dalla stazione idrometrica di riferimento,
# anche sullo stesso corso d'acqua, la lettura non viene più considerata
# rappresentativa della struttura (troppo lontana per essere un proxy
# accettabile senza un vero modello di routing) — DA VALIDARE.
DISTANZA_MASSIMA_RILEVANTE_KM = float(os.environ.get("SDREA_IDRO_DISTANZA_MASSIMA_KM", "40.0"))  # DA VALIDARE


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Identica alla versione nel motore sismico — nessuna dipendenza tra i due backend, duplicata per costruzione."""
    R_TERRA_KM = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R_TERRA_KM * math.asin(math.sqrt(a))


def deriva_livello_allerta(livello_m: float, soglia1: float | None, soglia2: float | None, soglia3: float | None) -> str:
    """
    Colore di allerta standard Protezione Civile, derivato dalle soglie
    ufficiali della stazione. Se le soglie non sono note per questa
    stazione, ritorna "sconosciuto" invece di inventare un colore — un
    dato mancante non deve mai travestirsi da "verde" (tutto ok).
    """
    if soglia3 is not None and livello_m >= soglia3:
        return "rosso"
    if soglia2 is not None and livello_m >= soglia2:
        return "arancione"
    if soglia1 is not None and livello_m >= soglia1:
        return "giallo"
    if soglia1 is not None:
        return "verde"
    return "sconosciuto"


def struttura_e_rilevante(corso_acqua_struttura: str | None, corso_acqua_stazione: str | None, distanza_km: float) -> bool:
    """
    Filtro esplicito (vedi nota nel docstring del modulo): una struttura è
    coinvolta solo se dichiara lo stesso corso d'acqua della stazione (o
    se manca il dato: in quel caso non possiamo escluderla con certezza,
    quindi valutata con incertezza dichiarata) e sta entro la distanza
    massima ritenuta rappresentativa del proxy.
    """
    if distanza_km > DISTANZA_MASSIMA_RILEVANTE_KM:
        return False
    if corso_acqua_struttura and corso_acqua_stazione:
        return corso_acqua_struttura.strip().lower() == corso_acqua_stazione.strip().lower()
    return True  # dato mancante da un lato: non escludiamo, ma vedi "incertezza_corso_acqua" nei fattori


def stima_impatto(
    livello_idrometrico_m: float,
    soglia1: float | None,
    soglia2: float | None,
    soglia3: float | None,
    quota_slm_m: float | None,
) -> dict:
    """
    Ritorna un dict con l'impatto stimato — non un singolo numero — così
    la spiegabilità (Vicky) può mostrare esattamente cosa ha contribuito,
    come nel motore sismico.

    `superamento_normalizzato`: quanto il livello supera soglia1 (inizio
    vigilanza) relativamente alla distanza soglia1->soglia3, in [0, ~1.5+].
    Se soglia1/soglia3 non sono note, usa il livello grezzo come fallback
    dichiarato meno affidabile (`stima_grezza=True` nel risultato).
    """
    livello_allerta = deriva_livello_allerta(livello_idrometrico_m, soglia1, soglia2, soglia3)

    stima_grezza = soglia1 is None or soglia3 is None
    if not stima_grezza:
        ampiezza = max(soglia3 - soglia1, 0.01)
        superamento_normalizzato = max((livello_idrometrico_m - soglia1) / ampiezza, 0.0)
    else:
        # Fallback dichiarato: senza soglie ufficiali per questa stazione,
        # normalizza su un livello di riferimento arbitrario (2m) solo per
        # avere un numero comparabile — MAI da usare in produzione senza
        # le soglie reali della stazione.
        superamento_normalizzato = max(livello_idrometrico_m / 2.0, 0.0)

    rischio_diretto_struttura = None
    if quota_slm_m is not None:
        rischio_diretto_struttura = (livello_idrometrico_m - quota_slm_m) >= -MARGINE_SICUREZZA_QUOTA_M

    return {
        "livello_allerta": livello_allerta,
        "superamento_normalizzato": round(superamento_normalizzato, 4),
        "stima_grezza_senza_soglie_ufficiali": stima_grezza,
        "rischio_diretto_struttura_per_quota": rischio_diretto_struttura,
    }
