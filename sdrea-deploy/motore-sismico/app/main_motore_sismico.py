"""
Backend "SDREA — Motore Emergenza Sismica Reale" (Parte B della spec).

Avvio locale:
    pip install -r requirements.txt
    export PONTI_DAGHI_BASE_URL=http://localhost:8000
    uvicorn app.main:app --reload --port 8001

Endpoint chiave:
    POST /api/auth/register, /api/auth/login, GET /api/auth/me   (B.5)
    POST /api/eventi/poll-ingv   — interroga il feed INGV reale, ingerisce
                                    eventi nuovi, calcola le priorità e le
                                    invia a Ponti-Daghi-Edifici (B.2 + B.3)
    POST /api/eventi/manuale     — inserisce un evento a mano (test, o un
                                    evento riportato da altra fonte prima
                                    che INGV lo pubblichi)
    GET  /api/eventi             — lista eventi ingeriti
    GET  /api/eventi/{id}/valutazioni  — priorità calcolate per un evento,
                                          ordinate (più critiche prima)
    GET  /api/valutazioni/{id}   — dettaglio con fattori (spiegabilità, B.4.4)
"""

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, EventoSismico, ValutazionePriorita, Operatore, SessionToken,
    SquadraLogistica, AssegnazioneRisorsa,
)
from app.attenuation import haversine_km, stima_intensita_pga
from app.priority import calcola_priorita
from app.ingv_client import fetch_recent_events
from app.ponti_daghi_client import get_readiness_feed, push_evento_struttura
from app.rbac import hash_password, verify_password, new_token, make_auth_dependency, richiede_permesso
from app.logistica import squadre_vicine
from app.vicky import rispondi as vicky_rispondi, genera_riepilogo_proattivo
from app.impatto_stimato import stima_impatto_su_comuni, stima_probabilita_repliche

# --- DA CONFIGURARE dall'operatività della sala operativa (non un
# parametro scientifico come l'attenuazione/le soglie di priorità, ma va
# comunque esternalizzato e non deciso qui — vedi B.4.2 della spec).
SOGLIA_MAGNITUDO_NOTIFICA_PROATTIVA = float(os.environ.get("SDREA_SOGLIA_MAGNITUDO_NOTIFICA", "4.0"))  # DA VALIDARE

_MODELS_NS = SimpleNamespace(EventoSismico=EventoSismico, ValutazionePriorita=ValutazionePriorita, SquadraLogistica=SquadraLogistica)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "sdrea_sismico.db")
engine = create_engine(f"sqlite:///{DB_PATH}")
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)

app = FastAPI(title="SDREA — Motore Emergenza Sismica Reale")

# CORS permissivo — stessa scelta e stessa nota di onestà del motore
# idraulico gemello: da restringere a un'origine precisa prima di
# qualunque esposizione oltre una demo pubblica di sola lettura.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz():
    """Ping leggero senza side-effect, usato per i keep-alive del deploy gratuito."""
    return {"status": "ok", "servizio": "sdrea-motore-sismico"}


get_current_operatore = make_auth_dependency(SessionLocal, Operatore, SessionToken)


# --------------------------------------------------------------------------
# Auth (B.5)
# --------------------------------------------------------------------------

class OperatoreRegister(BaseModel):
    nome: str
    email: str
    password: str
    ruolo: str = "operatore_civile"

    def validate_(self):
        if "@" not in self.email or "." not in self.email.split("@")[-1]:
            raise HTTPException(status_code=422, detail="Email non valida")
        if len(self.password) < 8:
            raise HTTPException(status_code=422, detail="Password troppo corta (minimo 8 caratteri)")
        if self.ruolo not in ("operatore_civile", "operatore_difesa", "amministratore"):
            raise HTTPException(status_code=422, detail="Ruolo non valido")


class OperatoreLogin(BaseModel):
    email: str
    password: str


@app.post("/api/auth/register")
def register(req: OperatoreRegister):
    req.validate_()
    db = SessionLocal()
    if db.query(Operatore).filter(Operatore.email == req.email).first():
        db.close()
        raise HTTPException(status_code=400, detail="Email già registrata")
    pw_hash, salt = hash_password(req.password)
    operatore = Operatore(nome=req.nome, email=req.email, password_hash=pw_hash, password_salt=salt, ruolo=req.ruolo)
    db.add(operatore)
    db.commit()
    db.refresh(operatore)
    token, expires_at = new_token()
    db.add(SessionToken(token=token, operatore_id=operatore.id, expires_at=expires_at))
    db.commit()
    result = {"token": token, "operatore_id": operatore.id, "nome": operatore.nome, "ruolo": operatore.ruolo}
    db.close()
    return result


@app.post("/api/auth/login")
def login(req: OperatoreLogin):
    db = SessionLocal()
    operatore = db.query(Operatore).filter(Operatore.email == req.email).first()
    if not operatore or not verify_password(req.password, operatore.password_hash, operatore.password_salt):
        db.close()
        raise HTTPException(status_code=401, detail="Credenziali non valide")
    if not operatore.attivo:
        db.close()
        raise HTTPException(status_code=401, detail="Utente disattivato")
    token, expires_at = new_token()
    db.add(SessionToken(token=token, operatore_id=operatore.id, expires_at=expires_at))
    db.commit()
    result = {"token": token, "operatore_id": operatore.id, "nome": operatore.nome, "ruolo": operatore.ruolo}
    db.close()
    return result


@app.get("/api/auth/me")
def me(operatore: Operatore = Depends(get_current_operatore)):
    return {"id": operatore.id, "nome": operatore.nome, "email": operatore.email, "ruolo": operatore.ruolo}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _valutazione_to_dict(v: ValutazionePriorita) -> dict:
    return {
        "id": v.id,
        "evento_id": v.evento_id,
        "structure_id": v.structure_id,
        "structure_nome": v.structure_nome,
        "distanza_km": round(v.distanza_km, 2),
        "intensita_stimata": v.intensita_stimata,
        "priorita_finale": v.priorita_finale,
        "fattori": v.fattori,
    }


def _evento_to_dict(e: EventoSismico) -> dict:
    return {
        "id": e.id,
        "ingv_event_id": e.ingv_event_id,
        "magnitudo": e.magnitudo,
        "lat_epicentro": e.lat_epicentro,
        "lon_epicentro": e.lon_epicentro,
        "profondita_km": e.profondita_km,
        "timestamp": e.timestamp.isoformat() if e.timestamp else None,
        "fonte": e.fonte,
        "luogo": e.luogo,
    }


def _processa_evento(db, evento: EventoSismico) -> list[ValutazionePriorita]:
    """
    B.2 + B.3: per un evento già salvato, recupera il readiness-feed di
    Ponti-Daghi-Edifici, calcola distanza/intensità/priorità per ogni
    struttura con coordinate, salva le ValutazionePriorita e prova a
    inviare EventoStruttura a Ponti-Daghi-Edifici per ognuna.

    Il fallimento del push verso Ponti-Daghi-Edifici NON cancella il
    lavoro già fatto qui (le valutazioni restano salvate e consultabili
    da Vicky/dall'operatore anche se Ponti-Daghi-Edifici è momentaneamente
    irraggiungibile) — viene solo segnalato nel risultato.
    """
    try:
        strutture = get_readiness_feed()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Impossibile recuperare il readiness-feed da Ponti-Daghi-Edifici: {exc}",
        )

    valutazioni = []
    push_falliti = []
    for s in strutture:
        if s.get("lat") is None or s.get("lon") is None:
            continue
        distanza = haversine_km(evento.lat_epicentro, evento.lon_epicentro, s["lat"], s["lon"])
        intensita = stima_intensita_pga(evento.magnitudo, distanza, evento.profondita_km)
        esito = calcola_priorita(
            intensita_stimata_g=intensita,
            readiness_colore=s.get("readiness"),
            ridondanza=s.get("ridondanza"),
            normativa_antisismica=s.get("normativa_antisismica"),
            edifici_sensibili_vicini=s.get("edifici_sensibili_vicini"),
            percorsi_alternativi=s.get("percorsi_alternativi"),
        )
        structure_id = int(str(s["id"]).replace("ponti-daghi-", ""))
        v = ValutazionePriorita(
            evento_id=evento.id,
            structure_id=structure_id,
            structure_nome=s.get("nome"),
            structure_lat=s["lat"],
            structure_lon=s["lon"],
            distanza_km=distanza,
            intensita_stimata=intensita,
            priorita_finale=esito["priorita_finale"],
            priorita_rank=esito["priorita_rank"],
            fattori=esito["fattori"],
        )
        db.add(v)
        valutazioni.append(v)

        try:
            push_evento_struttura(
                structure_id=structure_id,
                tipo_evento="sisma",
                data_iso=evento.timestamp.isoformat(),
                intensita_stimata=intensita,
                fonte=evento.fonte,
            )
        except Exception as exc:
            push_falliti.append({"structure_id": structure_id, "errore": str(exc)})

    db.commit()
    for v in valutazioni:
        db.refresh(v)
    return valutazioni, push_falliti


# --------------------------------------------------------------------------
# Eventi (B.2 + B.3)
# --------------------------------------------------------------------------

class EventoManualeRequest(BaseModel):
    ingv_event_id: str
    magnitudo: float
    lat_epicentro: float
    lon_epicentro: float
    profondita_km: float | None = None
    timestamp: datetime | None = None
    luogo: str | None = None
    fonte: str = "manuale"


@app.post("/api/eventi/manuale")
def crea_evento_manuale(req: EventoManualeRequest, operatore: Operatore = Depends(get_current_operatore)):
    """
    Permette di inserire un evento senza dipendere dal poll INGV — utile
    per test, per un evento riportato da un'altra fonte prima che compaia
    nel catalogo INGV, o per simulazioni in sala operativa. Richiede
    autenticazione (chi inserisce un evento che guida priorità operative
    deve essere identificabile).
    """
    richiede_permesso(operatore, "vedi_eventi")
    db = SessionLocal()
    if db.query(EventoSismico).filter(EventoSismico.ingv_event_id == req.ingv_event_id).first():
        db.close()
        raise HTTPException(status_code=400, detail="Un evento con questo ingv_event_id esiste già")
    evento = EventoSismico(
        ingv_event_id=req.ingv_event_id,
        magnitudo=req.magnitudo,
        lat_epicentro=req.lat_epicentro,
        lon_epicentro=req.lon_epicentro,
        profondita_km=req.profondita_km,
        timestamp=req.timestamp or datetime.now(timezone.utc).replace(tzinfo=None),
        fonte=req.fonte,
        luogo=req.luogo,
    )
    db.add(evento)
    db.commit()
    db.refresh(evento)
    valutazioni, push_falliti = _processa_evento(db, evento)
    result = {
        "evento": _evento_to_dict(evento),
        "n_strutture_valutate": len(valutazioni),
        "push_falliti_verso_ponti_daghi": push_falliti,
        "notifica_proattiva": genera_riepilogo_proattivo(evento, valutazioni) if evento.magnitudo >= SOGLIA_MAGNITUDO_NOTIFICA_PROATTIVA else None,
    }
    db.close()
    return result


@app.post("/api/eventi/poll-ingv")
def poll_ingv(min_magnitude: float = 2.0, operatore: Operatore = Depends(get_current_operatore)):
    """
    B.2 — Interroga il feed pubblico INGV per eventi dall'ultimo evento
    già ingerito (o dalle ultime 24h se il database è vuoto), ingerisce
    quelli nuovi (idempotente su ingv_event_id) e calcola le priorità per
    ciascuno. Pensato per essere richiamato periodicamente da uno
    scheduler esterno (cron, non incluso qui per restare un servizio
    stateless facile da testare — l'ingegnere può agganciarlo a qualunque
    scheduler già in uso).
    """
    richiede_permesso(operatore, "vedi_eventi")
    db = SessionLocal()
    ultimo = db.query(EventoSismico).order_by(EventoSismico.timestamp.desc()).first()
    if ultimo:
        starttime = ultimo.timestamp.isoformat()
    else:
        starttime = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)).isoformat()

    try:
        grezzi = fetch_recent_events(starttime, min_magnitude)
    except Exception as exc:
        db.close()
        raise HTTPException(status_code=502, detail=f"Impossibile interrogare il feed INGV: {exc}")

    nuovi_eventi = []
    for dati in grezzi:
        if db.query(EventoSismico).filter(EventoSismico.ingv_event_id == dati["ingv_event_id"]).first():
            continue  # già ingerito — idempotenza
        evento = EventoSismico(**dati)
        db.add(evento)
        db.commit()
        db.refresh(evento)
        nuovi_eventi.append(evento)

    riepilogo = []
    for evento in nuovi_eventi:
        valutazioni, push_falliti = _processa_evento(db, evento)
        riepilogo.append({
            "evento": _evento_to_dict(evento),
            "n_strutture_valutate": len(valutazioni),
            "push_falliti_verso_ponti_daghi": push_falliti,
            "notifica_proattiva": genera_riepilogo_proattivo(evento, valutazioni) if evento.magnitudo >= SOGLIA_MAGNITUDO_NOTIFICA_PROATTIVA else None,
        })

    db.close()
    return {"n_eventi_nuovi": len(nuovi_eventi), "eventi": riepilogo}


@app.get("/api/eventi")
def list_eventi(operatore: Operatore = Depends(get_current_operatore)):
    richiede_permesso(operatore, "vedi_eventi")
    db = SessionLocal()
    eventi = db.query(EventoSismico).order_by(EventoSismico.timestamp.desc()).all()
    result = [_evento_to_dict(e) for e in eventi]
    db.close()
    return result


@app.get("/api/eventi/{evento_id}/valutazioni")
def list_valutazioni(evento_id: int, operatore: Operatore = Depends(get_current_operatore)):
    """B.3 — lista ordinata, priorità più critiche prima (usata anche da B.4.1, Vicky)."""
    richiede_permesso(operatore, "vedi_priorita")
    db = SessionLocal()
    evento = db.query(EventoSismico).filter(EventoSismico.id == evento_id).first()
    if not evento:
        db.close()
        raise HTTPException(status_code=404, detail="Evento non trovato")
    valutazioni = (
        db.query(ValutazionePriorita)
        .filter(ValutazionePriorita.evento_id == evento_id)
        .order_by(ValutazionePriorita.priorita_rank.desc(), ValutazionePriorita.distanza_km.asc())
        .all()
    )
    result = {"evento": _evento_to_dict(evento), "valutazioni": [_valutazione_to_dict(v) for v in valutazioni]}
    db.close()
    return result


@app.get("/api/eventi/{evento_id}/impatto-stimato")
def get_impatto_stimato(evento_id: int, operatore: Operatore = Depends(get_current_operatore)):
    """
    Risposta rapida post-evento (vedi app/impatto_stimato.py): stima
    l'intensità di scuotimento attesa nei comuni di riferimento e la
    probabilità di repliche significative nelle prossime ore — pensata per
    dare una prima priorità di ispezione nei minuti successivi a un evento
    reale, prima che arrivi qualunque sopralluogo sul campo.
    """
    richiede_permesso(operatore, "vedi_eventi")
    db = SessionLocal()
    evento = db.query(EventoSismico).filter(EventoSismico.id == evento_id).first()
    db.close()
    if not evento:
        raise HTTPException(status_code=404, detail="Evento non trovato")

    impatto_comuni = stima_impatto_su_comuni(evento.magnitudo, evento.lat_epicentro, evento.lon_epicentro, evento.profondita_km)
    ore_trascorse = max((datetime.now(timezone.utc).replace(tzinfo=None) - evento.timestamp).total_seconds() / 3600.0, 0.0)
    repliche = stima_probabilita_repliche(evento.magnitudo, ore_trascorse, finestra_ore=24.0)

    return {
        "evento": _evento_to_dict(evento),
        "impatto_stimato_per_comune": impatto_comuni,
        "fuori_copertura_catalogo_comuni": len(impatto_comuni) == 0,
        "probabilita_repliche_prossime_24h": repliche,
        "nota": (
            "Stima automatica basata su un modello di attenuazione con coefficienti non ancora "
            "validati dal Dipartimento di Ingegneria (vedi app/attenuation.py) — da trattare come "
            "indicazione di priorità di massima, non come sostituto di un sopralluogo tecnico."
        ) + ("" if impatto_comuni else (
            " Nessun comune del catalogo attuale (area ferrarese) è entro un raggio utile da questo "
            "epicentro: il catalogo copre solo quell'area, non ancora tutta Italia."
        )),
    }


@app.get("/api/valutazioni/{valutazione_id}")
def get_valutazione(valutazione_id: int, operatore: Operatore = Depends(get_current_operatore)):
    """B.4.4 — dettaglio con `fattori`: la spiegazione del 'perché' per un operatore."""
    richiede_permesso(operatore, "vedi_priorita")
    db = SessionLocal()
    v = db.query(ValutazionePriorita).filter(ValutazionePriorita.id == valutazione_id).first()
    if not v:
        db.close()
        raise HTTPException(status_code=404, detail="Valutazione non trovata")
    result = _valutazione_to_dict(v)
    db.close()
    return result


@app.get("/api/eventi/{evento_id}/riepilogo")
def get_riepilogo_evento(evento_id: int, operatore: Operatore = Depends(get_current_operatore)):
    """B.4.2 — riepilogo proattivo per un evento specifico, indipendentemente dalla soglia di magnitudo."""
    richiede_permesso(operatore, "vedi_priorita")
    db = SessionLocal()
    evento = db.query(EventoSismico).filter(EventoSismico.id == evento_id).first()
    if not evento:
        db.close()
        raise HTTPException(status_code=404, detail="Evento non trovato")
    valutazioni = db.query(ValutazionePriorita).filter(ValutazionePriorita.evento_id == evento_id).all()
    result = genera_riepilogo_proattivo(evento, valutazioni)
    db.close()
    return result


# --------------------------------------------------------------------------
# Logistica (B.4.3) — vedi app/logistica.py e la nota onesta su M4 in app/models.py
# --------------------------------------------------------------------------

class SquadraCreate(BaseModel):
    nome: str
    tipo: str
    lat: float
    lon: float
    disponibile: bool = True
    note: str | None = None


@app.post("/api/logistica/squadre")
def crea_squadra(req: SquadraCreate, operatore: Operatore = Depends(get_current_operatore)):
    richiede_permesso(operatore, "vedi_priorita")
    db = SessionLocal()
    s = SquadraLogistica(nome=req.nome, tipo=req.tipo, lat=req.lat, lon=req.lon, disponibile=req.disponibile, note=req.note)
    db.add(s)
    db.commit()
    db.refresh(s)
    result = {"id": s.id, "nome": s.nome, "tipo": s.tipo, "lat": s.lat, "lon": s.lon, "disponibile": s.disponibile, "note": s.note}
    db.close()
    return result


@app.get("/api/logistica/squadre")
def list_squadre(operatore: Operatore = Depends(get_current_operatore)):
    richiede_permesso(operatore, "vedi_priorita")
    db = SessionLocal()
    squadre = db.query(SquadraLogistica).all()
    result = [{"id": s.id, "nome": s.nome, "tipo": s.tipo, "lat": s.lat, "lon": s.lon, "disponibile": s.disponibile, "note": s.note} for s in squadre]
    db.close()
    return result


@app.get("/api/logistica/squadre/vicine")
def get_squadre_vicine(lat: float, lon: float, limit: int = 5, operatore: Operatore = Depends(get_current_operatore)):
    richiede_permesso(operatore, "vedi_priorita")
    db = SessionLocal()
    squadre = db.query(SquadraLogistica).all()
    result = squadre_vicine(squadre, lat, lon, solo_disponibili=True, limit=limit)
    db.close()
    return result


# --------------------------------------------------------------------------
# Assegnazioni risorse — SEMPRE su conferma umana esplicita (vincolo B.4)
# --------------------------------------------------------------------------

class AssegnazioneConfermaRequest(BaseModel):
    squadra_id: int
    structure_id: int
    evento_id: int | None = None
    note: str | None = None


@app.post("/api/assegnazioni/conferma")
def conferma_assegnazione(req: AssegnazioneConfermaRequest, operatore: Operatore = Depends(get_current_operatore)):
    """
    Unico endpoint che scrive un'assegnazione di risorsa — e SOLO perché
    un operatore autenticato la sta confermando esplicitamente in questa
    stessa chiamata (`confermata_da_id`, obbligatorio, è sempre
    l'operatore del token usato per chiamare questo endpoint: non è un
    campo che il client può impostare a piacere). Non esiste alcun
    percorso automatico che arrivi qui da solo — vedi il vincolo esplicito
    su Vicky in B.4 della spec, applicato per costruzione, non solo per
    convenzione.
    """
    richiede_permesso(operatore, "vedi_priorita")
    db = SessionLocal()
    squadra = db.query(SquadraLogistica).filter(SquadraLogistica.id == req.squadra_id).first()
    if not squadra:
        db.close()
        raise HTTPException(status_code=404, detail="Squadra non trovata")
    assegnazione = AssegnazioneRisorsa(
        squadra_id=req.squadra_id,
        structure_id=req.structure_id,
        evento_id=req.evento_id,
        confermata_da_id=operatore.id,
        note=req.note,
    )
    db.add(assegnazione)
    db.commit()
    db.refresh(assegnazione)
    result = {
        "id": assegnazione.id,
        "squadra_id": assegnazione.squadra_id,
        "structure_id": assegnazione.structure_id,
        "evento_id": assegnazione.evento_id,
        "confermata_da": operatore.nome,
        "note": assegnazione.note,
    }
    db.close()
    return result


# --------------------------------------------------------------------------
# Vicky (B.4) — query in linguaggio naturale, sola lettura (vedi app/vicky.py)
# --------------------------------------------------------------------------

class VickyQueryRequest(BaseModel):
    domanda: str


@app.post("/api/vicky/query")
def vicky_query(req: VickyQueryRequest, operatore: Operatore = Depends(get_current_operatore)):
    richiede_permesso(operatore, "vedi_priorita")
    db = SessionLocal()
    result = vicky_rispondi(db, _MODELS_NS, req.domanda)
    db.close()
    return result
