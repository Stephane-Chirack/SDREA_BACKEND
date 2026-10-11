"""
Vicky per il backend "Ponti, Daghi ed Edifici a Rischio" (aggiunto
2026-10-11, terzo motore integrato nella cascata di Vicky — vedi
sdrea_script.js, VickyAI.tryEngines).

Stesso vincolo non negoziabile degli altri due motori (idraulico, sismico):
modulo di SOLA LETTURA. Nessuna funzione qui scrive nel database, nessun
colore di readiness/priorità viene mai inventato o "arrotondato" — tutto
quello che Vicky dice qui viene letto direttamente da Structure.readiness
(vedi app/models.py), la stessa regola trasparente già usata dal resto
dell'app, mai un secondo giudizio parallelo calcolato qui.

Niente integrazione LLM (a differenza di motore-idraulico/motore-sismico,
vedi app/vicky_llm.py lì): questo backend non ha configurazione Groq propria
e i dati sono semplici (inventario + stato), un riconoscimento a parole
chiave è sufficiente e non introduce una dipendenza esterna in più prima
del colloquio del 15/10. Stessa interfaccia {tipo, risposta, esempi} degli
altri due motori, così il frontend (RiskEngineClient.chiediAVicky) la tratta
in modo identico qualunque sia il motore che risponde.
"""

import re

ESEMPI_DOMANDE = [
    "quante strutture sono censite",
    "quali strutture sono in stato critico",
    "quali strutture sono da monitorare",
    "quali strutture non sono mai state ispezionate",
    "qual è lo stato di <nome struttura>",
]

# Termini che appartengono chiaramente al motore idraulico o al motore
# sismico, non a questo: se compaiono nella domanda, questo motore NON
# risponde (anche se contenesse parole come "rischio" o "pericolo" che da
# sole sarebbero ambigue) — stessa logica di esclusione reciproca già
# aggiunta a motore-idraulico/motore-sismico il 2026-10-10 per evitare che
# un motore "rubi" le domande di un altro.
_DOMINIO_ALTRUI = re.compile(
    r"livello idr|idrometric|fium|alluvion|piena|argine|allert.*meteo|bollettino|"
    r"sism|terremot|scoss|magnitudo|replica|repliche|ipocentro|epicentro"
)


def _tutte_strutture(db, Structure):
    return db.query(Structure).order_by(Structure.id.desc()).all()


def _nome_citato(strutture, domanda_lower):
    # Match esatto sul nome (case-insensitive) citato per intero nella
    # domanda — se più strutture hanno nomi che si sovrappongono (raro ma
    # possibile), si sceglie il nome più lungo che compare, per evitare che
    # un nome breve contenuto in uno più lungo prenda il sopravvento.
    trovate = [s for s in strutture if s.name and s.name.lower() in domanda_lower]
    if not trovate:
        return None
    trovate.sort(key=lambda s: len(s.name), reverse=True)
    return trovate[0]


def rispondi_struttura_specifica(db, Structure, domanda: str) -> dict:
    strutture = _tutte_strutture(db, Structure)
    s = _nome_citato(strutture, domanda.lower())
    if not s:
        nomi = ", ".join(st.name for st in strutture[:10])
        return {
            "tipo": "stato_struttura",
            "risposta": (
                f"Non ho riconosciuto una struttura nella domanda. Strutture note: {nomi}."
                if nomi else "Nessuna struttura censita finora."
            ),
        }
    r = s.readiness
    comune = s.comune or "comune non indicato"
    if s.inspections:
        ultima = s.inspections[0].data.date().isoformat()
        nota_ispezione = f", ultima ispezione il {ultima}"
    else:
        nota_ispezione = ", mai ispezionata"
    return {
        "tipo": "stato_struttura",
        "risposta": f"{s.name} ({comune}, {s.tipo}): stato {r['colore']} — {r['motivo']}{nota_ispezione}.",
    }


def rispondi_conteggio(db, Structure, domanda: str) -> dict:
    strutture = _tutte_strutture(db, Structure)
    if not strutture:
        return {"tipo": "conteggio", "risposta": "Nessuna struttura censita finora."}
    per_tipo = {}
    for s in strutture:
        per_tipo[s.tipo] = per_tipo.get(s.tipo, 0) + 1
    dettaglio = ", ".join(f"{v} {k}" for k, v in sorted(per_tipo.items()))
    return {
        "tipo": "conteggio",
        "risposta": f"{len(strutture)} strutture censite in totale ({dettaglio}).",
    }


def rispondi_mai_ispezionate(db, Structure, domanda: str) -> dict:
    strutture = _tutte_strutture(db, Structure)
    match = [s for s in strutture if not s.inspections]
    if not match:
        return {"tipo": "mai_ispezionate", "risposta": "Tutte le strutture censite hanno almeno un'ispezione registrata."}
    nomi = "; ".join(f"{s.name} ({s.comune or 'comune non indicato'})" for s in match[:10])
    extra = f" (+{len(match) - 10} altre)" if len(match) > 10 else ""
    return {
        "tipo": "mai_ispezionate",
        "risposta": f"{len(match)} strutture non hanno mai avuto un'ispezione: {nomi}{extra}.",
    }


def rispondi_stato_gruppo(db, Structure, domanda: str, colore: str) -> dict:
    strutture = _tutte_strutture(db, Structure)
    match = [s for s in strutture if s.readiness["colore"] == colore]
    if not match:
        return {"tipo": "stato_gruppo", "risposta": f"Nessuna struttura in stato {colore} al momento."}
    nomi = "; ".join(
        f"{s.name} ({s.comune or 'comune non indicato'}) — {s.readiness['motivo']}" for s in match[:8]
    )
    extra = f" (+{len(match) - 8} altre)" if len(match) > 8 else ""
    return {
        "tipo": "stato_gruppo",
        "risposta": f"{len(match)} strutture in stato {colore}: {nomi}{extra}.",
    }


def rispondi(db, Structure, domanda: str) -> dict:
    d = (domanda or "").lower().strip()
    if not d:
        return {"tipo": "sconosciuta", "risposta": "Domanda vuota.", "esempi": ESEMPI_DOMANDE}

    if _DOMINIO_ALTRUI.search(d):
        # Non è compito di questo motore (vedi nota sopra) — lasciamo che la
        # cascata del frontend (tryEngines) passi al motore giusto, o che la
        # risposta "sconosciuta" arrivi comunque con gli esempi di questo
        # motore se nessun altro ha riconosciuto la domanda.
        return {"tipo": "sconosciuta", "risposta": "Non ho riconosciuto questo tipo di domanda. Esempi di domande che capisco:", "esempi": ESEMPI_DOMANDE}

    strutture = _tutte_strutture(db, Structure)
    if _nome_citato(strutture, d) or re.search(r"stato di|com'è messa|come sta|condizioni di|readiness di", d):
        return rispondi_struttura_specifica(db, Structure, domanda)

    if re.search(r"quante strutture|quanti ponti|quante dighe|quanti edifici|strutture censit|censimento", d):
        return rispondi_conteggio(db, Structure, domanda)

    if re.search(r"mai ispezionat|nessuna ispezione|senza ispezion|non.*ispezionat", d):
        return rispondi_mai_ispezionate(db, Structure, domanda)

    if re.search(r"stato critico|in rosso|priorit|ispezione prioritaria|rischio|pericol|urgent", d):
        return rispondi_stato_gruppo(db, Structure, domanda, "rosso")

    if re.search(r"giall|da monitorare|attenzione", d):
        return rispondi_stato_gruppo(db, Structure, domanda, "giallo")

    return {
        "tipo": "sconosciuta",
        "risposta": "Non ho riconosciuto questo tipo di domanda. Esempi di domande che capisco:",
        "esempi": ESEMPI_DOMANDE,
    }
