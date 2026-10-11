"""
B.4 — Nuove capacità di Vicky per il motore sismico.

Vincolo esplicito della spec, non negoziabile e applicato qui per
costruzione (non solo come commento): "Vicky supporta la decisione
dell'operatore umano, non la sostituisce. Nessuna funzionalità deve
permettere l'invio automatico di squadre o l'attivazione di procedure
senza conferma umana." Coerentemente:
- Questo modulo è SOLO di lettura: nessuna funzione qui scrive nel
  database (crea eventi, assegna squadre, ecc.). Risponde a domande,
  punto.
- L'unico modo di assegnare una risorsa a una struttura è
  POST /api/assegnazioni/conferma in app/main.py, che richiede sempre un
  operatore autenticato — Vicky può SUGGERIRE la squadra più vicina
  (rispondi_squadre_vicine), mai assegnarla.

Nota sull'interpretazione del linguaggio naturale — aggiornata il
2026-10-10: il riconoscimento di intento resta a parole chiave/pattern
come base (sufficiente da solo per le query di esempio della spec,
B.4.1-B.4.3, e usato comunque come fallback), ma `rispondi()` ora prova
PRIMA a usare un vero LLM (vedi app/vicky_llm.py, provider Groq, piano
gratuito) per classificare l'intento e per riformulare la risposta finale
in italiano naturale. L'LLM non interroga mai da solo il database e non
genera mai un fatto: riformula solo i dati che le funzioni rispondi_*
qui sotto hanno già recuperato per davvero. Se GROQ_API_KEY non è
configurata, o la chiamata fallisce per qualunque motivo (rete, quota,
risposta malformata), `rispondi()` ricade silenziosamente sul
comportamento originale a parole chiave — mai un errore per l'operatore,
mai una funzionalità che si rompe perché un servizio gratuito esterno è
irraggiungibile. Quando la domanda non corrisponde a nessun pattern
riconosciuto (né dal LLM né dalle parole chiave), Vicky lo dice
esplicitamente e mostra esempi — non inventa mai una risposta plausibile
su dati che non ha interrogato (B.4.1: "non un riassunto generico").
"""

import re
from datetime import datetime, timezone

from app.logistica import squadre_vicine
from app.attenuation import haversine_km
from app.impatto_stimato import stima_impatto_su_comuni, stima_probabilita_repliche
from app import vicky_llm

ESEMPI_DOMANDE = [
    "quali infrastrutture richiedono ispezione prioritaria",
    "quali strutture sono in priorità critica",
    "quali squadre sono disponibili più vicine a <nome struttura>",
    "perché <nome struttura> è in priorità critica",
    "quale comune è più esposto per l'ultimo evento",
    "probabilità di repliche",
]


def _ultimo_evento(db, EventoSismico):
    return db.query(EventoSismico).order_by(EventoSismico.timestamp.desc()).first()


def _valutazioni_evento(db, ValutazionePriorita, evento_id, solo_priorita=None):
    q = db.query(ValutazionePriorita).filter(ValutazionePriorita.evento_id == evento_id)
    if solo_priorita:
        q = q.filter(ValutazionePriorita.priorita_finale.in_(solo_priorita))
    return q.order_by(ValutazionePriorita.priorita_rank.desc(), ValutazionePriorita.distanza_km.asc()).all()


def rispondi_priorita(db, EventoSismico, ValutazionePriorita, domanda: str) -> dict:
    """
    B.4.1 — "quali infrastrutture nell'area dell'evento di oggi richiedono
    ispezione prioritaria" e varianti: interroga ValutazionePriorita per
    l'evento più recente (o un evento specifico se la domanda ne cita
    l'id) e risponde con la lista ordinata — mai un riassunto generico.
    """
    evento = _ultimo_evento(db, EventoSismico)
    if not evento:
        return {"tipo": "priorita", "risposta": "Nessun evento sismico registrato finora.", "valutazioni": []}

    # priorità richieste esplicitamente nella domanda ("critica", "alta"...)
    priorita_richieste = [p for p in ("critica", "alta", "media", "bassa") if p in domanda.lower()]
    valutazioni = _valutazioni_evento(db, ValutazionePriorita, evento.id, priorita_richieste or None)

    if not valutazioni:
        filtro = f" con priorità {', '.join(priorita_richieste)}" if priorita_richieste else ""
        return {
            "tipo": "priorita",
            "evento_id": evento.id,
            "risposta": f"Nessuna struttura{filtro} per l'evento del {evento.timestamp.isoformat()} ({evento.luogo or 'luogo non specificato'}).",
            "valutazioni": [],
        }

    lista = [
        f"{v.structure_nome or v.structure_id} — priorità {v.priorita_finale} (distanza {v.distanza_km:.1f} km, intensità stimata {v.intensita_stimata:.3f} g)"
        for v in valutazioni
    ]
    return {
        "tipo": "priorita",
        "evento_id": evento.id,
        "risposta": f"Evento del {evento.timestamp.isoformat()} ({evento.luogo or 'luogo non specificato'}, M{evento.magnitudo}): {len(valutazioni)} strutture, in ordine di priorità:\n" + "\n".join(lista),
        "valutazioni": [
            {"structure_id": v.structure_id, "structure_nome": v.structure_nome, "priorita_finale": v.priorita_finale, "distanza_km": v.distanza_km, "valutazione_id": v.id}
            for v in valutazioni
        ],
    }


def rispondi_squadre_vicine(db, ValutazionePriorita, SquadraLogistica, EventoSismico, domanda: str) -> dict:
    """
    B.4.3 — "quali squadre/mezzi sono disponibili più vicini alla
    struttura X": cerca X nel nome delle strutture già valutate
    (dall'ultimo evento, dove ne conosciamo le coordinate) e interroga il
    modulo Logistica. Se la struttura non è identificabile dal testo, lo
    dice esplicitamente invece di indovinare.
    """
    evento = _ultimo_evento(db, EventoSismico)
    if not evento:
        return {"tipo": "squadre_vicine", "risposta": "Nessun evento sismico registrato: non ho coordinate di strutture da cui cercare squadre vicine."}

    valutazioni = _valutazioni_evento(db, ValutazionePriorita, evento.id)
    domanda_lower = domanda.lower()
    match = None
    for v in valutazioni:
        if v.structure_nome and v.structure_nome.lower() in domanda_lower:
            match = v
            break

    if not match:
        nomi = ", ".join(sorted({v.structure_nome for v in valutazioni if v.structure_nome}))
        return {
            "tipo": "squadre_vicine",
            "risposta": f"Non ho riconosciuto una struttura nella domanda. Strutture note dall'ultimo evento: {nomi}." if nomi else "Nessuna struttura nota da cui cercare.",
        }

    squadre = db.query(SquadraLogistica).all()
    vicine = squadre_vicine(squadre, match.structure_lat, match.structure_lon, solo_disponibili=True, limit=5)
    if not vicine:
        return {
            "tipo": "squadre_vicine",
            "structure_id": match.structure_id,
            "structure_nome": match.structure_nome,
            "risposta": f"Nessuna squadra/mezzo disponibile registrata vicino a {match.structure_nome}. Ricorda: questa è solo una proposta — l'assegnazione richiede sempre conferma di un operatore (POST /api/assegnazioni/conferma).",
            "squadre": [],
        }

    lista = [f"{s['nome']} ({s['tipo']}) — {s['distanza_km']} km" for s in vicine]
    return {
        "tipo": "squadre_vicine",
        "structure_id": match.structure_id,
        "structure_nome": match.structure_nome,
        "risposta": f"Squadre/mezzi disponibili più vicini a {match.structure_nome}:\n" + "\n".join(lista) + "\n\n(Proposta — l'assegnazione richiede sempre conferma di un operatore, non viene mai fatta automaticamente.)",
        "squadre": vicine,
    }


def rispondi_spiegazione(db, ValutazionePriorita, EventoSismico, domanda: str) -> dict:
    """
    B.4.4 — "perché questa struttura è in priorità critica": cerca la
    struttura citata tra le valutazioni dell'ultimo evento e restituisce
    `fattori` per intero — mai una risposta a scatola chiusa.
    """
    evento = _ultimo_evento(db, EventoSismico)
    if not evento:
        return {"tipo": "spiegazione", "risposta": "Nessun evento sismico registrato."}

    valutazioni = _valutazioni_evento(db, ValutazionePriorita, evento.id)
    domanda_lower = domanda.lower()
    match = None
    for v in valutazioni:
        if v.structure_nome and v.structure_nome.lower() in domanda_lower:
            match = v
            break

    if not match:
        nomi = ", ".join(sorted({v.structure_nome for v in valutazioni if v.structure_nome}))
        return {"tipo": "spiegazione", "risposta": f"Non ho riconosciuto una struttura nella domanda. Strutture note: {nomi}." if nomi else "Nessuna struttura nota."}

    f = match.fattori or {}
    righe = [f"Priorità assegnata: {match.priorita_finale} (score {f.get('score_totale', '?')})"]
    for chiave, valore in f.items():
        if chiave in ("score_totale", "soglie_usate"):
            continue
        if valore:
            righe.append(f"- {chiave}: {valore}")
    if f.get("soglie_usate"):
        righe.append(f"Soglie usate per la classificazione: {f['soglie_usate']}")

    return {
        "tipo": "spiegazione",
        "structure_id": match.structure_id,
        "structure_nome": match.structure_nome,
        "risposta": "\n".join(righe),
        "fattori": f,
    }


def rispondi_impatto(db, EventoSismico, domanda: str) -> dict:
    """
    Risposta rapida post-evento (vedi app/impatto_stimato.py): quale comune
    è più esposto e quanto è probabile una replica significativa nelle
    prossime 24h, per l'ULTIMO evento reale ingerito. Stessa logica
    dell'endpoint REST GET /api/eventi/{id}/impatto-stimato, così Vicky e
    il pannello non raccontano mai due cose diverse.
    """
    evento = _ultimo_evento(db, EventoSismico)
    if not evento:
        return {"tipo": "impatto_stimato", "risposta": "Nessun evento sismico registrato finora."}

    impatto = stima_impatto_su_comuni(evento.magnitudo, evento.lat_epicentro, evento.lon_epicentro, evento.profondita_km)
    ore_trascorse = max((datetime.now(timezone.utc).replace(tzinfo=None) - evento.timestamp).total_seconds() / 3600.0, 0.0)
    repliche = stima_probabilita_repliche(evento.magnitudo, ore_trascorse, finestra_ore=24.0)

    top3 = impatto[:3]
    righe = [
        f"Ultimo evento: M{evento.magnitudo} — {evento.luogo or 'localizzazione non specificata'} "
        f"({evento.timestamp.isoformat()})."
    ]
    righe.append("Comuni di riferimento più esposti (stima, coefficienti da validare): " + ", ".join(
        f"{c['comune']} ({c['intensita_percepita_stimata']}, {c['pga_stimata_g']} g a {c['distanza_epicentro_km']} km)"
        for c in top3
    ) + ".")
    righe.append(
        f"Probabilità di almeno una replica ≥M{repliche['magnitudo_soglia_replica']} nelle prossime "
        f"{int(repliche['finestra_ore'])}h: {repliche['probabilita_almeno_una_replica_pct']}% (stima statistica generica, non un evento previsto)."
    )
    return {
        "tipo": "impatto_stimato",
        "risposta": " ".join(righe),
        "impatto_stimato_per_comune": impatto,
        "probabilita_repliche": repliche,
    }


_TIPI_NOTI = ["spiegazione", "squadre_vicine", "impatto_stimato", "priorita"]

# 2026-10-10: descrizioni passate alla classificazione LLM (vedi
# vicky_llm.classifica_intento) — senza queste, una domanda diretta e
# generica come "è pericoloso?" o "c'è un rischio sismico?" (nessuna
# parola in comune con gli esempi o con i nomi dei tipi) poteva non
# essere classificata, cadendo sul fallback a parole chiave che aveva lo
# stesso identico problema (vedi le regex sotto, ora ampliate).
_DESCRIZIONI_TIPI = {
    "spiegazione": "perché una specifica struttura ha una certa priorità — richiede che la domanda citi il nome della struttura",
    "squadre_vicine": "quali squadre/mezzi sono disponibili vicino a una specifica struttura",
    "impatto_stimato": "quanto è grave/pericoloso l'ultimo evento sismico nel suo complesso: comuni più esposti, probabilità di repliche — è il tipo giusto per una domanda generica tipo \"è pericoloso?\", \"che rischio c'è?\", \"quanto è forte?\" quando non cita una struttura specifica. NON usare questo tipo se la domanda parla chiaramente del livello di un fiume/di un'alluvione (es. \"rischio alluvione\", \"livello del fiume\") — quella non è una domanda di competenza di questo motore sismico: in quel caso rispondi con un tipo che non è nell'elenco.",
    "priorita": "quali strutture richiedono ispezione prioritaria per l'ultimo evento, in elenco",
}


def rispondi(db, models_ns, domanda: str) -> dict:
    """
    Dispatcher unico (vedi nota nel docstring del modulo). models_ns è un
    piccolo namespace con EventoSismico/ValutazionePriorita/SquadraLogistica
    per evitare un import circolare con app.main.

    Prova prima la classificazione via LLM (vicky_llm.classifica_intento):
    se non configurata o fallita, `tipo` resta None e il comportamento è
    identico, riga per riga, a prima del 2026-10-10 — stesso ordine di
    pattern, stesso fallback "sconosciuta". Solo le funzioni rispondi_*
    interrogano davvero il database: l'LLM qui sopra sceglie solo quale
    chiamare.
    """
    d = domanda.lower().strip()
    if not d:
        return {"tipo": "sconosciuta", "risposta": "Domanda vuota.", "esempi": ESEMPI_DOMANDE}

    classificazione = vicky_llm.classifica_intento(domanda, _TIPI_NOTI, ESEMPI_DOMANDE, _DESCRIZIONI_TIPI)
    tipo = classificazione["tipo"] if classificazione else None

    if tipo == "spiegazione" or (tipo is None and re.search(r"\bperch[eé]\b", d)):
        risultato = rispondi_spiegazione(db, models_ns.ValutazionePriorita, models_ns.EventoSismico, domanda)
    elif tipo == "squadre_vicine" or (tipo is None and re.search(r"squadr|mezz|risors", d) and re.search(r"vicin|disponibil", d)):
        risultato = rispondi_squadre_vicine(db, models_ns.ValutazionePriorita, models_ns.SquadraLogistica, models_ns.EventoSismico, domanda)
    elif tipo == "impatto_stimato" or (
        tipo is None
        and re.search(r"replic|assestamento|impatto|comune|esposto|esposti|scuotimento|rischio|rischi|pericol|grave|forte|sicur", d)
        and not re.search(r"fium|idr(o|aulic)|alluvion|piena|argine|allert.*meteo|bollettino", d)
    ):
        # 2026-10-10: aggiunte "rischio/pericolo/grave/forte/sicur*" — una
        # domanda diretta tipo "è pericoloso?", "c'è rischio?", "è grave?"
        # non condivide nessuna parola con le regex originali sopra e
        # restava "sconosciuta" anche col fallback LLM fallito/non
        # configurato. impatto_stimato è il tipo più vicino quando la
        # domanda non cita una struttura specifica (altrimenti vince
        # "spiegazione"/"squadre_vicine" già sopra, controllati prima).
        # 2026-10-11: esclusione esplicita delle domande chiaramente
        # idrauliche — specchio della stessa esclusione in
        # motore-idraulico/app/vicky.py, per non "rubare" domande sul
        # fiume prima che la cascata del frontend arrivi al motore giusto.
        risultato = rispondi_impatto(db, models_ns.EventoSismico, domanda)
    elif tipo == "priorita" or (tipo is None and re.search(r"priorit|ispezion|infrastruttur|strutture", d)):
        risultato = rispondi_priorita(db, models_ns.EventoSismico, models_ns.ValutazionePriorita, domanda)
    else:
        return {
            "tipo": "sconosciuta",
            "risposta": "Non ho riconosciuto questo tipo di domanda. Esempi di domande che capisco:",
            "esempi": ESEMPI_DOMANDE,
        }

    # Riformulazione naturale, SOLO sui fatti già recuperati sopra — se
    # fallisce, risultato["risposta"] resta il template originale, già
    # completo e corretto (vedi vicky_llm.fraseggia).
    riformulata = vicky_llm.fraseggia(domanda, risultato)
    if riformulata:
        risultato["risposta"] = riformulata
    return risultato


def genera_riepilogo_proattivo(evento, valutazioni) -> dict:
    """
    B.4.2 — Notifica proattiva: riepilogo generato quando un evento supera
    la soglia di magnitudo configurabile (vedi app/main.py per dove
    questa soglia è definita e per il fatto che è configurabile, non
    hardcoded). Puro supporto decisionale: nessuna azione viene presa qui,
    solo un riepilogo testuale/strutturato per l'operatore in sala
    operativa.
    """
    n_critiche = sum(1 for v in valutazioni if v.priorita_finale == "critica")
    n_alte = sum(1 for v in valutazioni if v.priorita_finale == "alta")
    top = sorted(valutazioni, key=lambda v: v.priorita_rank, reverse=True)[:1]
    prima_azione = (
        f"Prima azione consigliata: dare priorità all'ispezione di '{top[0].structure_nome}' (priorità {top[0].priorita_finale}, a {top[0].distanza_km:.1f} km dall'epicentro)."
        if top else "Nessuna struttura valutata per questo evento."
    )
    return {
        "evento_id": evento.id,
        "magnitudo": evento.magnitudo,
        "luogo": evento.luogo,
        "n_strutture_coinvolte": len(valutazioni),
        "n_priorita_critica": n_critiche,
        "n_priorita_alta": n_alte,
        "prima_azione_consigliata": prima_azione,
        "nota": "Riepilogo a supporto della decisione dell'operatore umano — nessuna azione automatica è stata eseguita.",
    }
