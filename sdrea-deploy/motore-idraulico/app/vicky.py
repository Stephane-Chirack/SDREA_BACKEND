"""
Vicky per il motore idraulico — stesso vincolo non negoziabile del motore
sismico, applicato per costruzione: modulo di sola lettura, nessuna
funzione qui scrive nel database. L'unico modo di assegnare una risorsa è
POST /api/assegnazioni/conferma in app/main.py, sempre con operatore
autenticato.

Aggiornamento 2026-10-10: `rispondi()` ora prova prima un vero LLM (vedi
app/vicky_llm.py, provider Groq, piano gratuito) per classificare
l'intento e riformulare la risposta finale in italiano naturale — mai per
recuperare dati, solo per instradare e per fraseggiare quelli già
recuperati dalle funzioni qui sotto. Se GROQ_API_KEY non è configurata o
la chiamata fallisce, si ricade silenziosamente sullo stesso
riconoscimento a parole chiave di sempre: stesso comportamento, stesso
ordine, nessuna eccezione verso l'operatore.
"""

import re

from app.logistica import squadre_vicine
from app.previsione_idrometrica import estrai_serie_storica, stima_trend_livello
from app import vicky_llm

ESEMPI_DOMANDE = [
    "quali infrastrutture richiedono ispezione prioritaria",
    "quali strutture sono in priorità critica",
    "quali squadre sono disponibili più vicine a <nome struttura>",
    "perché <nome struttura> è in priorità critica",
    "qual è il livello di allerta attuale",
    "che previsione c'è per il livello del fiume",
]


def _ultimo_evento(db, EventoIdraulico):
    return db.query(EventoIdraulico).order_by(EventoIdraulico.timestamp.desc()).first()


def _valutazioni_evento(db, ValutazionePrioritaIdraulica, evento_id, solo_priorita=None):
    q = db.query(ValutazionePrioritaIdraulica).filter(ValutazionePrioritaIdraulica.evento_id == evento_id)
    if solo_priorita:
        q = q.filter(ValutazionePrioritaIdraulica.priorita_finale.in_(solo_priorita))
    return q.order_by(ValutazionePrioritaIdraulica.priorita_rank.desc(), ValutazionePrioritaIdraulica.distanza_km.asc()).all()


def rispondi_priorita(db, EventoIdraulico, ValutazionePrioritaIdraulica, domanda: str) -> dict:
    evento = _ultimo_evento(db, EventoIdraulico)
    if not evento:
        return {"tipo": "priorita", "risposta": "Nessuna lettura idrometrica critica registrata finora.", "valutazioni": []}

    priorita_richieste = [p for p in ("critica", "alta", "media", "bassa") if p in domanda.lower()]
    valutazioni = _valutazioni_evento(db, ValutazionePrioritaIdraulica, evento.id, priorita_richieste or None)

    if not valutazioni:
        filtro = f" con priorità {', '.join(priorita_richieste)}" if priorita_richieste else ""
        return {
            "tipo": "priorita",
            "evento_id": evento.id,
            "risposta": f"Nessuna struttura{filtro} per la lettura del {evento.timestamp.isoformat()} su {evento.nome_stazione or evento.codice_stazione} ({evento.corso_d_acqua or 'corso d’acqua non specificato'}).",
            "valutazioni": [],
        }

    lista = [
        f"{v.structure_nome or v.structure_id} — priorità {v.priorita_finale} (livello oltre soglia: {v.livello_relativo_m:+.2f} m, distanza dalla stazione {v.distanza_km:.1f} km)"
        for v in valutazioni
    ]
    return {
        "tipo": "priorita",
        "evento_id": evento.id,
        "risposta": (
            f"Lettura del {evento.timestamp.isoformat()} — stazione {evento.nome_stazione or evento.codice_stazione} "
            f"({evento.corso_d_acqua or 'corso d’acqua non specificato'}), livello {evento.livello_idrometrico_m} m, "
            f"allerta {evento.livello_allerta or 'sconosciuta'}: {len(valutazioni)} strutture, in ordine di priorità:\n"
            + "\n".join(lista)
        ),
        "valutazioni": [
            {"structure_id": v.structure_id, "structure_nome": v.structure_nome, "priorita_finale": v.priorita_finale, "distanza_km": v.distanza_km, "valutazione_id": v.id}
            for v in valutazioni
        ],
    }


def rispondi_squadre_vicine(db, ValutazionePrioritaIdraulica, SquadraLogistica, EventoIdraulico, domanda: str) -> dict:
    evento = _ultimo_evento(db, EventoIdraulico)
    if not evento:
        return {"tipo": "squadre_vicine", "risposta": "Nessuna lettura idrometrica registrata: non ho coordinate di strutture da cui cercare squadre vicine."}

    valutazioni = _valutazioni_evento(db, ValutazionePrioritaIdraulica, evento.id)
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
            "risposta": f"Non ho riconosciuto una struttura nella domanda. Strutture note dall'ultima lettura: {nomi}." if nomi else "Nessuna struttura nota da cui cercare.",
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


def rispondi_spiegazione(db, ValutazionePrioritaIdraulica, EventoIdraulico, domanda: str) -> dict:
    evento = _ultimo_evento(db, EventoIdraulico)
    if not evento:
        return {"tipo": "spiegazione", "risposta": "Nessuna lettura idrometrica registrata."}

    valutazioni = _valutazioni_evento(db, ValutazionePrioritaIdraulica, evento.id)
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
    if f.get("incertezza_corso_acqua"):
        righe.append("Attenzione: manca il dato 'corso_acqua_riferimento' per questa struttura — la valutazione non ha potuto confermare che sia sullo stesso corso d'acqua della stazione.")
    if f.get("soglie_usate"):
        righe.append(f"Soglie usate per la classificazione: {f['soglie_usate']}")

    return {
        "tipo": "spiegazione",
        "structure_id": match.structure_id,
        "structure_nome": match.structure_nome,
        "risposta": "\n".join(righe),
        "fattori": f,
    }


def rispondi_livello_allerta(db, EventoIdraulico, domanda: str, LetturaBollettinoMonitoraggio=None) -> dict:
    """
    Query specifica del dominio idraulico, senza equivalente nel motore
    sismico: un operatore in sala operativa spesso vuole sapere prima di
    tutto "a che punto siamo" (verde/giallo/arancione/rosso) prima di
    guardare le singole strutture.

    EventoIdraulico resta vuoto finché ARPA_IDRO_BASE_URL non è configurato
    (nessuna fonte per-stazione con soglie ufficiali pubblicamente stabile
    trovata — vedi app/arpa_client.py). La fonte reale oggi collegata dal
    frontend è invece il bollettino di monitoraggio regionale (vedi
    app/allerta_meteo_client.py, tabella LetturaBollettinoMonitoraggio):
    prima di arrendersi con "nessuna lettura", questa funzione controlla
    anche quella — altrimenti Vicky direbbe "dati insufficienti" mentre
    l'interfaccia mostra già un livello reale, cosa già successa e
    scorretta.
    """
    evento = _ultimo_evento(db, EventoIdraulico)
    if evento:
        return {
            "tipo": "livello_allerta",
            "risposta": (
                f"Ultima lettura: stazione {evento.nome_stazione or evento.codice_stazione} "
                f"({evento.corso_d_acqua or 'corso d’acqua non specificato'}), livello {evento.livello_idrometrico_m} m "
                f"del {evento.timestamp.isoformat()} — livello di allerta: {evento.livello_allerta or 'sconosciuto (soglie non disponibili per questa stazione)'}."
            ),
            "livello_allerta": evento.livello_allerta,
            "codice_stazione": evento.codice_stazione,
        }
    if LetturaBollettinoMonitoraggio is not None:
        lettura = _ultima_riga(db, LetturaBollettinoMonitoraggio, LetturaBollettinoMonitoraggio.fetch_at)
        if lettura:
            return {
                "tipo": "livello_allerta",
                "risposta": (
                    f"Ultima lettura (bollettino di monitoraggio {lettura.bollettino_id}, Allerta Meteo "
                    f"Emilia-Romagna): {lettura.nome_bacino} — {lettura.nome_sottobacino}, livello osservato "
                    f"{lettura.livello_osservato if lettura.livello_osservato is not None else 'non disponibile'} m "
                    f"({lettura.ora_osservazione_raw or 'ora non disponibile'})."
                    + (f" Colmo di piena previsto: {lettura.livello_colmo_previsto} m ({lettura.ora_colmo_previsto_raw})." if lettura.livello_colmo_previsto is not None else "")
                    + " Nota: questo bollettino non classifica un colore di allerta per singola stazione."
                ),
                "livello_allerta": None,
                "nome_sottobacino": lettura.nome_sottobacino,
            }
    return {
        "tipo": "livello_allerta",
        "risposta": (
            "Nessuna lettura idrometrica registrata. Esegui POST /api/eventi/poll-arpa (se "
            "ARPA_IDRO_BASE_URL è configurato) o /api/eventi/poll-bollettino-monitoraggio "
            "(fonte regionale Emilia-Romagna, sempre disponibile) per acquisire un dato reale."
        ),
    }


def _ultima_riga(db, Model, order_col):
    return db.query(Model).order_by(order_col.desc()).first()


def rispondi_stato_ufficiale(db, StatoAllertaZona, LetturaBollettinoMonitoraggio, EventoIdraulico, domanda: str) -> dict:
    """
    Incrocia i dati: mette a confronto, per lo stesso momento, (1) il
    colore di allerta ufficiale per zona (fonte: Allerta Meteo
    Emilia-Romagna, /o/get-stato-allerta), (2) le letture numeriche più
    recenti del bollettino di monitoraggio idrologico ufficiale (stessa
    fonte, /o/get-bollettino-monitoraggio) e (3), se presente, l'ultima
    lettura di stazione ingerita nel motore (EventoIdraulico, oggi
    popolata solo da inserimento manuale o da una fonte ARPA configurata a
    parte — vedi app/arpa_client.py). Richiede che almeno un poll delle
    fonti regionali sia stato eseguito (POST /api/eventi/poll-stato-allerta
    e /api/eventi/poll-bollettino-monitoraggio): se il database è vuoto lo
    dice esplicitamente invece di rispondere con un falso "tutto verde".
    """
    ultimo_bollettino = _ultima_riga(db, StatoAllertaZona, StatoAllertaZona.fetch_at)
    if not ultimo_bollettino:
        return {
            "tipo": "stato_ufficiale",
            "risposta": (
                "Nessun dato ufficiale regionale ancora acquisito in questa sessione. "
                "Esegui POST /api/eventi/poll-stato-allerta (e opzionalmente "
                "/api/eventi/poll-bollettino-monitoraggio) per interrogare le fonti reali."
            ),
        }

    data_emissione = ultimo_bollettino.data_emissione_raw
    zone_allerta = (
        db.query(StatoAllertaZona)
        .filter(StatoAllertaZona.data_emissione_raw == data_emissione)
        .all()
    )
    zone_idrauliche = [z for z in zone_allerta if z.fenomeno == "idraulica"]
    zone_non_verdi = [z for z in zone_idrauliche if z.colore not in ("verde", "sconosciuto")]

    righe = [f"Bollettino di vigilanza ufficiale (Allerta Meteo Emilia-Romagna): \"{ultimo_bollettino.titolo_bollettino}\""]
    if zone_non_verdi:
        for z in zone_non_verdi:
            righe.append(f"- Zona {z.zona}: allerta idraulica {z.colore}")
    else:
        righe.append(f"- Nessuna zona in allerta idraulica sopra il verde (su {len(zone_idrauliche)} zone monitorate).")

    ultima_lettura_bollettino = _ultima_riga(db, LetturaBollettinoMonitoraggio, LetturaBollettinoMonitoraggio.fetch_at)
    if ultima_lettura_bollettino:
        letture_stesso_bollettino = (
            db.query(LetturaBollettinoMonitoraggio)
            .filter(LetturaBollettinoMonitoraggio.bollettino_id == ultima_lettura_bollettino.bollettino_id)
            .all()
        )
        n_bacini = len({l.nome_bacino for l in letture_stesso_bollettino if l.nome_bacino})
        righe.append(
            f"Bollettino di monitoraggio idrologico {ultima_lettura_bollettino.bollettino_id}: "
            f"{len(letture_stesso_bollettino)} letture numeriche su {n_bacini} bacini."
        )
    else:
        righe.append("Nessuna lettura del bollettino di monitoraggio ancora acquisita (POST /api/eventi/poll-bollettino-monitoraggio).")

    ultimo_evento_stazione = _ultima_riga(db, EventoIdraulico, EventoIdraulico.timestamp)
    if ultimo_evento_stazione:
        righe.append(
            f"Ultima lettura di stazione nel motore: {ultimo_evento_stazione.nome_stazione or ultimo_evento_stazione.codice_stazione} "
            f"— allerta {ultimo_evento_stazione.livello_allerta or 'sconosciuta'} (fonte: {ultimo_evento_stazione.fonte})."
        )
        if zone_idrauliche:
            colori_zona = {z.colore for z in zone_idrauliche}
            if ultimo_evento_stazione.livello_allerta and colori_zona and ultimo_evento_stazione.livello_allerta not in colori_zona and "verde" not in colori_zona:
                righe.append(
                    "Attenzione — incrocio dati: il colore della lettura di stazione non coincide con nessuna delle "
                    "zone ufficiali attualmente non verdi. Verificare manualmente prima di agire."
                )
    else:
        righe.append("Nessuna lettura di stazione (EventoIdraulico) ancora presente nel motore.")

    return {
        "tipo": "stato_ufficiale",
        "risposta": "\n".join(righe),
        "data_emissione_bollettino": data_emissione,
        "zone_idrauliche_non_verdi": [{"zona": z.zona, "colore": z.colore} for z in zone_non_verdi],
        "fonte": "Allerta Meteo Emilia-Romagna (Agenzia regionale protezione civile) — dati ufficiali reali",
    }


def rispondi_previsione(db, LetturaBollettinoMonitoraggio, domanda: str) -> dict:
    """
    Domanda esplicita sulla previsione/tendenza del livello del fiume.
    Riusa esattamente la stessa logica del backend REST
    GET /api/previsione/idrometrica (app/previsione_idrometrica.py) così
    che Vicky e il pannello non possano mai raccontare due cose diverse.
    Il sottobacino è dedotto dalla domanda quando possibile (cerca un nome
    noto), altrimenti usa Pontelagoscuro (Ferrara), coerente con la
    richiesta esplicita del fondatore di dare priorità a Ferrara.
    """
    if LetturaBollettinoMonitoraggio is None:
        return {
            "tipo": "previsione",
            "risposta": "Il modello dati di questo motore non espone letture del bollettino di monitoraggio.",
        }
    sottobacino = "Pontelagoscuro"
    match = re.search(r"a ([a-zàèéìòù' ]{3,30})\??$", domanda.lower().strip())
    if match:
        candidato = match.group(1).strip().title()
        esiste = (
            db.query(LetturaBollettinoMonitoraggio)
            .filter(LetturaBollettinoMonitoraggio.nome_sottobacino.ilike(f"%{candidato}%"))
            .first()
        )
        if esiste:
            sottobacino = esiste.nome_sottobacino

    letture = estrai_serie_storica(db, LetturaBollettinoMonitoraggio, sottobacino, n=6)
    trend = stima_trend_livello(letture)
    ultima = letture[-1] if letture else None

    if not trend.get("disponibile"):
        return {
            "tipo": "previsione",
            "risposta": f"Nessuna serie di osservazioni sufficiente per {sottobacino}. {trend.get('motivo', '')}",
            "sottobacino": sottobacino,
        }

    righe = [
        f"Sottobacino {sottobacino}: ultimo livello osservato {trend['livello_osservato_ultimo']} m, "
        f"tendenza {trend['tendenza']} (calcolata su {trend['n_osservazioni_usate']} osservazioni ufficiali)."
    ]
    if trend.get("livello_stimato_prossima_osservazione") is not None:
        righe.append(
            f"Stima SDREA per la prossima osservazione: {trend['livello_stimato_prossima_osservazione']} m "
            f"± {trend['margine_incertezza_m']} m — non è un modello idraulico del Po, è un'estrapolazione "
            f"statistica di brevissimo termine."
        )
    if ultima and ultima.livello_colmo_previsto is not None:
        righe.append(
            f"Il bollettino ufficiale prevede invece un colmo di piena di {ultima.livello_colmo_previsto} m "
            f"({ultima.ora_colmo_previsto_raw}): questo è il valore autorevole da usare per le decisioni operative."
        )
    return {
        "tipo": "previsione",
        "risposta": " ".join(righe),
        "sottobacino": sottobacino,
        "trend": trend,
    }


_TIPI_NOTI = ["spiegazione", "squadre_vicine", "previsione", "stato_ufficiale", "livello_allerta", "priorita"]

# 2026-10-10: descrizioni passate alla classificazione LLM (vedi
# vicky_llm.classifica_intento) — senza queste, una domanda diretta e
# generica come "è pericoloso?" o "c'è rischio adesso?" (nessuna parola
# in comune con gli esempi o con i nomi dei tipi) poteva non essere
# classificata, cadendo sul fallback a parole chiave che aveva lo stesso
# identico problema (vedi le regex sotto, ora ampliate).
_DESCRIZIONI_TIPI = {
    "spiegazione": "perché una specifica struttura ha una certa priorità — richiede che la domanda citi il nome della struttura",
    "squadre_vicine": "quali squadre/mezzi sono disponibili vicino a una specifica struttura",
    "previsione": "come evolverà il livello del fiume nelle prossime ore (tendenza/previsione)",
    "stato_ufficiale": "incrocio tra il colore di allerta ufficiale regionale e i dati numerici del motore, su più zone/bacini",
    "livello_allerta": "qual è la situazione idrometrica ADESSO in generale: livello attuale, colore di allerta — è il tipo giusto per una domanda generica tipo \"è pericoloso?\", \"che rischio c'è?\", \"a che punto siamo?\" quando non cita una struttura specifica. NON usare questo tipo se la domanda parla chiaramente di un terremoto/evento sismico (es. \"rischio sismico\", \"è pericoloso il sisma?\") — quella non è una domanda di competenza di questo motore idraulico: in quel caso rispondi con un tipo che non è nell'elenco.",
    "priorita": "quali strutture richiedono ispezione prioritaria per l'ultima lettura, in elenco",
}


def rispondi(db, models_ns, domanda: str) -> dict:
    """
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
        risultato = rispondi_spiegazione(db, models_ns.ValutazionePrioritaIdraulica, models_ns.EventoIdraulico, domanda)
    elif tipo == "squadre_vicine" or (tipo is None and re.search(r"squadr|mezz|risors", d) and re.search(r"vicin|disponibil", d)):
        risultato = rispondi_squadre_vicine(db, models_ns.ValutazionePrioritaIdraulica, models_ns.SquadraLogistica, models_ns.EventoIdraulico, domanda)
    elif tipo == "previsione" or (tipo is None and re.search(r"previs|tendenz|trend|salir|scender|colmo", d)):
        risultato = rispondi_previsione(db, getattr(models_ns, "LetturaBollettinoMonitoraggio", None), domanda)
    elif tipo == "stato_ufficiale" or (tipo is None and re.search(r"ufficial|region|bollettino|incroci", d)):
        risultato = rispondi_stato_ufficiale(db, models_ns.StatoAllertaZona, models_ns.LetturaBollettinoMonitoraggio, models_ns.EventoIdraulico, domanda)
    elif tipo == "livello_allerta" or (
        tipo is None
        and re.search(r"livello di allerta|che allerta|colore di allerta|a che punto|livello idr|idrometric|rischio|rischi|pericol|grave|sicur", d)
        and not re.search(r"sismic|terremot|scoss|magnitudo|replica|repliche|ipocentro|epicentro", d)
    ):
        # 2026-10-10: aggiunte "rischio/pericolo/grave/sicur*" — una
        # domanda diretta tipo "è pericoloso?", "c'è rischio?", "è grave?"
        # non condivide nessuna parola con le regex originali sopra e
        # restava "sconosciuta" anche col fallback LLM fallito/non
        # configurato. livello_allerta è il tipo più vicino quando la
        # domanda non cita una struttura specifica (altrimenti vince
        # "spiegazione"/"squadre_vicine" già sopra, controllati prima).
        # 2026-10-11: esclusione esplicita delle domande chiaramente
        # sismiche — "rischio"/"pericolo" da soli facevano rispondere
        # SEMPRE questo motore idraulico anche a "rischio sismico?",
        # intercettando la domanda prima che la cascata del frontend
        # potesse provare il motore sismico reale (vedi anche la stessa
        # esclusione, specchiata, in motore-sismico/app/vicky.py).
        risultato = rispondi_livello_allerta(db, models_ns.EventoIdraulico, domanda, getattr(models_ns, "LetturaBollettinoMonitoraggio", None))
    elif tipo == "priorita" or (tipo is None and re.search(r"priorit|ispezion|infrastruttur|strutture", d)):
        risultato = rispondi_priorita(db, models_ns.EventoIdraulico, models_ns.ValutazionePrioritaIdraulica, domanda)
    else:
        return {
            "tipo": "sconosciuta",
            "risposta": "Non ho riconosciuto questo tipo di domanda. Esempi di domande che capisco:",
            "esempi": ESEMPI_DOMANDE + ["qual è lo stato ufficiale regionale (incrocio dati)"],
        }

    riformulata = vicky_llm.fraseggia(domanda, risultato)
    if riformulata:
        risultato["risposta"] = riformulata
    return risultato


def genera_riepilogo_proattivo(evento, valutazioni) -> dict:
    """Notifica proattiva — stesso ruolo del B.4.2 sismico, quando una lettura entra in allerta arancione/rossa."""
    n_critiche = sum(1 for v in valutazioni if v.priorita_finale == "critica")
    n_alte = sum(1 for v in valutazioni if v.priorita_finale == "alta")
    top = sorted(valutazioni, key=lambda v: v.priorita_rank, reverse=True)[:1]
    prima_azione = (
        f"Prima azione consigliata: dare priorità all'ispezione di '{top[0].structure_nome}' (priorità {top[0].priorita_finale}, a {top[0].distanza_km:.1f} km dalla stazione)."
        if top else "Nessuna struttura valutata per questa lettura."
    )
    return {
        "evento_id": evento.id,
        "codice_stazione": evento.codice_stazione,
        "corso_d_acqua": evento.corso_d_acqua,
        "livello_idrometrico_m": evento.livello_idrometrico_m,
        "livello_allerta": evento.livello_allerta,
        "n_strutture_coinvolte": len(valutazioni),
        "n_priorita_critica": n_critiche,
        "n_priorita_alta": n_alte,
        "prima_azione_consigliata": prima_azione,
        "nota": "Riepilogo a supporto della decisione dell'operatore umano — nessuna azione automatica è stata eseguita.",
    }
