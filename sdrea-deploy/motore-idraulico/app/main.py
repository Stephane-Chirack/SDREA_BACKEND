"""
Backend "SDREA — Motore Emergenza Idraulica Reale" (rischio alluvionale).

Stessa architettura del motore sismico (sdrea-motore-sismico/), stesso
principio non negoziabile: database SQLite proprio, nessun import di
codice da/verso gli altri due backend, solo HTTP.

Avvio locale:
    pip install -r requirements.txt
    export PONTI_DAGHI_BASE_URL=http://localhost:8000
    export ARPA_IDRO_BASE_URL=...   # vedi app/arpa_client.py — opzionale per i test manuali
    uvicorn app.main:app --reload --port 8002

Endpoint chiave:
    POST /api/auth/register, /api/auth/login, GET /api/auth/me
    POST /api/eventi/poll-arpa   — interroga ARPA_IDRO_BASE_URL (se configurato),
                                    ingerisce letture nuove, calcola le priorità
                                    e le invia a Ponti-Daghi-Edifici
    POST /api/eventi/manuale     — inserisce una lettura idrometrica a mano
                                    (test, demo, o fonte diversa da ARPA)
    POST /api/eventi/poll-stato-allerta         — interroga la API pubblica REALE
                                    di Allerta Meteo Emilia-Romagna (stato di
                                    allerta ufficiale per zona/fenomeno) — vedi
                                    app/allerta_meteo_client.py
    GET  /api/stato-allerta                     — ultimo stato salvato per zona/fenomeno
    POST /api/eventi/poll-bollettino-monitoraggio — interroga il bollettino di
                                    monitoraggio idrologico ufficiale (letture
                                    numeriche reali per sottobacino)
    GET  /api/bollettino-monitoraggio           — ultime letture salvate
    GET  /api/eventi             — lista letture ingerite
    GET  /api/eventi/{id}/valutazioni  — priorità calcolate per una lettura
    GET  /api/valutazioni/{id}   — dettaglio con fattori (spiegabilità)
    POST /api/vicky/query        — domande in linguaggio naturale, sola lettura
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
    Base, EventoIdraulico, ValutazionePrioritaIdraulica, Operatore, SessionToken,
    SquadraLogistica, AssegnazioneRisorsa, StatoAllertaZona, LetturaBollettinoMonitoraggio,
)
from app.idrometria import haversine_km, deriva_livello_allerta, struttura_e_rilevante, stima_impatto
from app.priority import calcola_priorita
from app.arpa_client import fetch_recent_levels
from app.allerta_meteo_client import (
    fetch_stato_allerta, parse_stato_allerta, fetch_bollettino_monitoraggio, parse_bollettino_monitoraggio,
)
from app.ponti_daghi_client import get_readiness_feed, push_evento_struttura
from app.rbac import hash_password, verify_password, new_token, make_auth_dependency, richiede_permesso
from app.logistica import squadre_vicine
from app.vicky import rispondi as vicky_rispondi, genera_riepilogo_proattivo
from app.previsione_idrometrica import (
    estrai_serie_storica, stima_trend_livello, fetch_previsione_pioggia, valuta_rischio_pluviale_locale,
    FERRARA_LAT, FERRARA_LON,
)

# DA CONFIGURARE dall'operatività della sala operativa — notifica proattiva
# quando una lettura entra in allerta pari o superiore a questo colore.
LIVELLI_ORDINE = {"verde": 0, "giallo": 1, "arancione": 2, "rosso": 3, "sconosciuto": 0}
LIVELLO_NOTIFICA_PROATTIVA = os.environ.get("SDREA_IDRO_LIVELLO_NOTIFICA", "arancione")  # DA VALIDARE

_MODELS_NS = SimpleNamespace(
    EventoIdraulico=EventoIdraulico, ValutazionePrioritaIdraulica=ValutazionePrioritaIdraulica,
    SquadraLogistica=SquadraLogistica, StatoAllertaZona=StatoAllertaZona,
    LetturaBollettinoMonitoraggio=LetturaBollettinoMonitoraggio,
)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "sdrea_idraulico.db")
engine = create_engine(f"sqlite:///{DB_PATH}")
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)

app = FastAPI(title="SDREA — Motore Emergenza Idraulica Reale")

# CORS permissivo — SOLO per consentire alla pagina HTML di test locale
# (aperta come file:// o servita da un server statico su un'altra porta) di
# chiamare questa API dal browser. Da restringere a un'origine precisa
# prima di qualunque esposizione oltre la rete di sviluppo locale.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

get_current_operatore = make_auth_dependency(SessionLocal, Operatore, SessionToken)


@app.get("/healthz")
def healthz():
    """Ping leggero senza side-effect, usato per i keep-alive del deploy gratuito."""
    return {"status": "ok", "servizio": "sdrea-motore-idraulico"}


# --------------------------------------------------------------------------
# Auth
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

def _valutazione_to_dict(v: ValutazionePrioritaIdraulica) -> dict:
    return {
        "id": v.id,
        "evento_id": v.evento_id,
        "structure_id": v.structure_id,
        "structure_nome": v.structure_nome,
        "distanza_km": round(v.distanza_km, 2),
        "livello_relativo_m": v.livello_relativo_m,
        "priorita_finale": v.priorita_finale,
        "fattori": v.fattori,
    }


def _evento_to_dict(e: EventoIdraulico) -> dict:
    return {
        "id": e.id,
        "lettura_id": e.lettura_id,
        "codice_stazione": e.codice_stazione,
        "nome_stazione": e.nome_stazione,
        "corso_d_acqua": e.corso_d_acqua,
        "lat_stazione": e.lat_stazione,
        "lon_stazione": e.lon_stazione,
        "livello_idrometrico_m": e.livello_idrometrico_m,
        "soglia1_ordinaria_m": e.soglia1_ordinaria_m,
        "soglia2_moderata_m": e.soglia2_moderata_m,
        "soglia3_elevata_m": e.soglia3_elevata_m,
        "livello_allerta": e.livello_allerta,
        "timestamp": e.timestamp.isoformat() if e.timestamp else None,
        "fonte": e.fonte,
    }


def _processa_evento(db, evento: EventoIdraulico) -> tuple[list[ValutazionePrioritaIdraulica], list[dict]]:
    """
    Per una lettura già salvata: recupera il readiness-feed di
    Ponti-Daghi-Edifici, filtra le strutture rilevanti per corso d'acqua/
    distanza (vedi app/idrometria.py), calcola priorità e prova a inviare
    EventoStruttura per ognuna. Un push fallito non cancella la
    valutazione già salvata — stessa logica del motore sismico.
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
        distanza = haversine_km(evento.lat_stazione, evento.lon_stazione, s["lat"], s["lon"])
        corso_struttura = s.get("corso_acqua_riferimento")
        if not struttura_e_rilevante(corso_struttura, evento.corso_d_acqua, distanza):
            continue

        impatto = stima_impatto(
            livello_idrometrico_m=evento.livello_idrometrico_m,
            soglia1=evento.soglia1_ordinaria_m,
            soglia2=evento.soglia2_moderata_m,
            soglia3=evento.soglia3_elevata_m,
            quota_slm_m=s.get("quota_slm_m"),
        )
        esito = calcola_priorita(
            superamento_normalizzato=impatto["superamento_normalizzato"],
            rischio_diretto_struttura_per_quota=impatto["rischio_diretto_struttura_per_quota"],
            zona_pgra=s.get("zona_pgra"),
            presenza_argine_protezione=s.get("presenza_argine_protezione"),
            readiness_colore=s.get("readiness"),
            edifici_sensibili_vicini=s.get("edifici_sensibili_vicini"),
            percorsi_alternativi=s.get("percorsi_alternativi"),
            incertezza_corso_acqua=not corso_struttura,
        )
        structure_id = int(str(s["id"]).replace("ponti-daghi-", ""))
        livello_riferimento = evento.soglia1_ordinaria_m if evento.soglia1_ordinaria_m is not None else 0.0
        v = ValutazionePrioritaIdraulica(
            evento_id=evento.id,
            structure_id=structure_id,
            structure_nome=s.get("nome"),
            structure_lat=s["lat"],
            structure_lon=s["lon"],
            distanza_km=distanza,
            livello_relativo_m=round(evento.livello_idrometrico_m - livello_riferimento, 3),
            priorita_finale=esito["priorita_finale"],
            priorita_rank=esito["priorita_rank"],
            fattori=esito["fattori"],
        )
        db.add(v)
        valutazioni.append(v)

        try:
            push_evento_struttura(
                structure_id=structure_id,
                tipo_evento="alluvione",
                data_iso=evento.timestamp.isoformat(),
                intensita_stimata=impatto["superamento_normalizzato"],
                fonte=evento.fonte,
            )
        except Exception as exc:
            push_falliti.append({"structure_id": structure_id, "errore": str(exc)})

    db.commit()
    for v in valutazioni:
        db.refresh(v)
    return valutazioni, push_falliti


def _superamento_livello_notifica(livello_allerta: str | None) -> bool:
    return LIVELLI_ORDINE.get(livello_allerta or "sconosciuto", 0) >= LIVELLI_ORDINE.get(LIVELLO_NOTIFICA_PROATTIVA, 2)


# --------------------------------------------------------------------------
# Eventi (letture idrometriche)
# --------------------------------------------------------------------------

class EventoManualeRequest(BaseModel):
    lettura_id: str | None = None
    codice_stazione: str
    nome_stazione: str | None = None
    corso_d_acqua: str | None = None
    lat_stazione: float
    lon_stazione: float
    livello_idrometrico_m: float
    soglia1_ordinaria_m: float | None = None
    soglia2_moderata_m: float | None = None
    soglia3_elevata_m: float | None = None
    timestamp: datetime | None = None
    fonte: str = "manuale"


@app.post("/api/eventi/manuale")
def crea_evento_manuale(req: EventoManualeRequest, operatore: Operatore = Depends(get_current_operatore)):
    """
    Permette di inserire una lettura senza dipendere da ARPA_IDRO_BASE_URL
    — utile per test, demo, o una lettura riportata da un'altra fonte.
    Richiede autenticazione: chi inserisce un dato che guida priorità
    operative deve essere identificabile.
    """
    richiede_permesso(operatore, "vedi_eventi")
    ts = req.timestamp or datetime.now(timezone.utc).replace(tzinfo=None)
    lettura_id = req.lettura_id or f"{req.codice_stazione}@{ts.isoformat()}"

    db = SessionLocal()
    if db.query(EventoIdraulico).filter(EventoIdraulico.lettura_id == lettura_id).first():
        db.close()
        raise HTTPException(status_code=400, detail="Una lettura con questo lettura_id esiste già")

    livello_allerta = deriva_livello_allerta(req.livello_idrometrico_m, req.soglia1_ordinaria_m, req.soglia2_moderata_m, req.soglia3_elevata_m)
    evento = EventoIdraulico(
        lettura_id=lettura_id,
        codice_stazione=req.codice_stazione,
        nome_stazione=req.nome_stazione,
        corso_d_acqua=req.corso_d_acqua,
        lat_stazione=req.lat_stazione,
        lon_stazione=req.lon_stazione,
        livello_idrometrico_m=req.livello_idrometrico_m,
        soglia1_ordinaria_m=req.soglia1_ordinaria_m,
        soglia2_moderata_m=req.soglia2_moderata_m,
        soglia3_elevata_m=req.soglia3_elevata_m,
        livello_allerta=livello_allerta,
        timestamp=ts,
        fonte=req.fonte,
    )
    db.add(evento)
    db.commit()
    db.refresh(evento)
    valutazioni, push_falliti = _processa_evento(db, evento)
    result = {
        "evento": _evento_to_dict(evento),
        "n_strutture_valutate": len(valutazioni),
        "push_falliti_verso_ponti_daghi": push_falliti,
        "notifica_proattiva": genera_riepilogo_proattivo(evento, valutazioni) if _superamento_livello_notifica(livello_allerta) else None,
    }
    db.close()
    return result


@app.post("/api/eventi/poll-arpa")
def poll_arpa(operatore: Operatore = Depends(get_current_operatore)):
    """
    Interroga ARPA_IDRO_BASE_URL (vedi app/arpa_client.py per la nota
    importante: non è ancora collegato a una fonte reale in questa
    consegna — va scelto e configurato dall'ingegnere). Se non
    configurato, ritorna 502 con un messaggio esplicito invece di un
    errore di rete generico.
    """
    richiede_permesso(operatore, "vedi_eventi")
    db = SessionLocal()
    try:
        grezzi = fetch_recent_levels()
    except Exception as exc:
        db.close()
        raise HTTPException(status_code=502, detail=str(exc))

    nuove_letture = []
    for dati in grezzi:
        if db.query(EventoIdraulico).filter(EventoIdraulico.lettura_id == dati["lettura_id"]).first():
            continue  # già ingerita — idempotenza
        livello_allerta = deriva_livello_allerta(dati["livello_idrometrico_m"], dati["soglia1_ordinaria_m"], dati["soglia2_moderata_m"], dati["soglia3_elevata_m"])
        evento = EventoIdraulico(**dati, livello_allerta=livello_allerta)
        db.add(evento)
        db.commit()
        db.refresh(evento)
        nuove_letture.append(evento)

    riepilogo = []
    for evento in nuove_letture:
        valutazioni, push_falliti = _processa_evento(db, evento)
        riepilogo.append({
            "evento": _evento_to_dict(evento),
            "n_strutture_valutate": len(valutazioni),
            "push_falliti_verso_ponti_daghi": push_falliti,
            "notifica_proattiva": genera_riepilogo_proattivo(evento, valutazioni) if _superamento_livello_notifica(evento.livello_allerta) else None,
        })

    db.close()
    return {"n_letture_nuove": len(nuove_letture), "eventi": riepilogo}


def _stato_allerta_to_dict(s: StatoAllertaZona) -> dict:
    return {
        "id": s.id,
        "zona": s.zona,
        "fenomeno": s.fenomeno,
        "colore": s.colore,
        "titolo_bollettino": s.titolo_bollettino,
        "link_bollettino": s.link_bollettino,
        "data_emissione": s.data_emissione_raw,
        "data_inizio": s.data_inizio_raw,
        "data_fine": s.data_fine_raw,
        "fetch_at": s.fetch_at.isoformat() if s.fetch_at else None,
        "fonte": s.fonte,
    }


def _lettura_bollettino_to_dict(l: LetturaBollettinoMonitoraggio) -> dict:
    return {
        "id": l.id,
        "bollettino_id": l.bollettino_id,
        "nome_bacino": l.nome_bacino,
        "nome_sottobacino": l.nome_sottobacino,
        "livello_osservato": l.livello_osservato,
        "ora_osservazione": l.ora_osservazione_raw,
        "livello_colmo_previsto": l.livello_colmo_previsto,
        "ora_colmo_previsto": l.ora_colmo_previsto_raw,
        "nota_bollettino": l.nota_bollettino,
        "fetch_at": l.fetch_at.isoformat() if l.fetch_at else None,
        "fonte": l.fonte,
    }


@app.post("/api/eventi/poll-stato-allerta")
def poll_stato_allerta(operatore: Operatore = Depends(get_current_operatore)):
    """
    Interroga la API pubblica reale di Allerta Meteo Emilia-Romagna
    (/o/get-stato-allerta — vedi app/allerta_meteo_client.py per la nota
    sulla verifica effettuata) e salva lo stato corrente per zona/fenomeno.
    Idempotente su (data_emissione, zona, fenomeno): un nuovo poll dello
    stesso bollettino non crea righe duplicate.
    """
    richiede_permesso(operatore, "vedi_eventi")
    db = SessionLocal()
    try:
        payload = fetch_stato_allerta()
    except Exception as exc:
        db.close()
        raise HTTPException(status_code=502, detail=f"Impossibile interrogare Allerta Meteo Emilia-Romagna: {exc}")

    righe = parse_stato_allerta(payload)
    nuove = []
    for r in righe:
        esiste = (
            db.query(StatoAllertaZona)
            .filter(
                StatoAllertaZona.data_emissione_raw == r["data_emissione_raw"],
                StatoAllertaZona.zona == r["zona"],
                StatoAllertaZona.fenomeno == r["fenomeno"],
            )
            .first()
        )
        if esiste:
            continue
        s = StatoAllertaZona(
            zona=r["zona"], fenomeno=r["fenomeno"], colore=r["colore"], colore_raw=r["colore_raw"],
            titolo_bollettino=r["titolo_bollettino"], link_bollettino=r["link_bollettino"],
            data_emissione_raw=r["data_emissione_raw"], data_inizio_raw=r["data_inizio_raw"],
            data_fine_raw=r["data_fine_raw"], fetch_at=r["fetch_at"], fonte=r["fonte"],
        )
        db.add(s)
        nuove.append(s)
    db.commit()
    for s in nuove:
        db.refresh(s)
    result = {"n_righe_nuove": len(nuove), "righe": [_stato_allerta_to_dict(s) for s in nuove]}
    db.close()
    return result


@app.get("/api/stato-allerta")
def get_stato_allerta_corrente(zona: str | None = None, fenomeno: str | None = None, operatore: Operatore = Depends(get_current_operatore)):
    """Ultimo stato salvato per zona/fenomeno (dopo almeno un poll-stato-allerta)."""
    richiede_permesso(operatore, "vedi_eventi")
    db = SessionLocal()
    q = db.query(StatoAllertaZona)
    if zona:
        q = q.filter(StatoAllertaZona.zona == zona)
    if fenomeno:
        q = q.filter(StatoAllertaZona.fenomeno == fenomeno)
    righe = q.order_by(StatoAllertaZona.fetch_at.desc()).limit(200).all()
    result = [_stato_allerta_to_dict(s) for s in righe]
    db.close()
    return result


@app.post("/api/eventi/poll-bollettino-monitoraggio")
def poll_bollettino_monitoraggio(operatore: Operatore = Depends(get_current_operatore)):
    """
    Interroga /o/get-bollettino-monitoraggio (vedi app/allerta_meteo_client.py)
    e salva le letture numeriche per sottobacino. Idempotente su
    (bollettino_id, nome_sottobacino, ora_osservazione).
    """
    richiede_permesso(operatore, "vedi_eventi")
    db = SessionLocal()
    try:
        payload = fetch_bollettino_monitoraggio()
    except Exception as exc:
        db.close()
        raise HTTPException(status_code=502, detail=f"Impossibile interrogare il bollettino di monitoraggio: {exc}")

    righe = parse_bollettino_monitoraggio(payload)
    nuove = []
    for r in righe:
        esiste = (
            db.query(LetturaBollettinoMonitoraggio)
            .filter(
                LetturaBollettinoMonitoraggio.bollettino_id == r["bollettino_id"],
                LetturaBollettinoMonitoraggio.nome_sottobacino == r["nome_sottobacino"],
                LetturaBollettinoMonitoraggio.ora_osservazione_raw == r["ora_osservazione_raw"],
            )
            .first()
        )
        if esiste:
            continue
        l = LetturaBollettinoMonitoraggio(
            bollettino_id=r["bollettino_id"], nome_bacino=r["nome_bacino"], nome_sottobacino=r["nome_sottobacino"],
            livello_osservato=r["livello_osservato"], ora_osservazione_raw=r["ora_osservazione_raw"],
            livello_colmo_previsto=r["livello_colmo_previsto"], ora_colmo_previsto_raw=r["ora_colmo_previsto_raw"],
            inizio_monitoraggio_raw=r["inizio_monitoraggio_raw"], fine_monitoraggio_raw=r["fine_monitoraggio_raw"],
            nota_bollettino=r["nota_bollettino"], fetch_at=r["fetch_at"], fonte=r["fonte"],
        )
        db.add(l)
        nuove.append(l)
    db.commit()
    for l in nuove:
        db.refresh(l)
    result = {"n_letture_nuove": len(nuove), "letture": [_lettura_bollettino_to_dict(l) for l in nuove]}
    db.close()
    return result


@app.get("/api/bollettino-monitoraggio")
def get_bollettino_monitoraggio_corrente(bacino: str | None = None, operatore: Operatore = Depends(get_current_operatore)):
    richiede_permesso(operatore, "vedi_eventi")
    db = SessionLocal()
    q = db.query(LetturaBollettinoMonitoraggio)
    if bacino:
        q = q.filter(LetturaBollettinoMonitoraggio.nome_bacino.ilike(f"%{bacino}%"))
    righe = q.order_by(LetturaBollettinoMonitoraggio.fetch_at.desc()).limit(500).all()
    result = [_lettura_bollettino_to_dict(l) for l in righe]
    db.close()
    return result


@app.get("/api/previsione/idrometrica")
def get_previsione_idrometrica(
    sottobacino: str = "Pontelagoscuro",
    lat: float | None = None,
    lon: float | None = None,
    operatore: Operatore = Depends(get_current_operatore),
):
    """
    Previsione a brevissimo termine per un sottobacino, PIÙ un indicatore
    di rischio pluviale locale indipendente — vedi la nota di principio in
    app/previsione_idrometrica.py: i due segnali non vengono mai fusi in un
    unico numero, perché rispondono a due fenomeni fisici diversi (piena
    del Po vs. allagamento urbano locale).

    `lat`/`lon` opzionali per il rischio pluviale locale: se omessi, usa
    Ferrara (richiesta esplicita: priorità a Ferrara, estendibile ad altre
    città passando le coordinate).
    """
    richiede_permesso(operatore, "vedi_eventi")
    db = SessionLocal()
    try:
        letture = estrai_serie_storica(db, LetturaBollettinoMonitoraggio, sottobacino, n=6)
        trend = stima_trend_livello(letture)

        ultima = letture[-1] if letture else None
        colmo_ufficiale = None
        if ultima and ultima.livello_colmo_previsto is not None:
            colmo_ufficiale = {
                "livello_colmo_previsto_m": ultima.livello_colmo_previsto,
                "ora_colmo_previsto": ultima.ora_colmo_previsto_raw,
                "fonte": "Allerta Meteo Emilia-Romagna — bollettino di monitoraggio ufficiale (autorevole)",
            }
    finally:
        db.close()

    pluviale = None
    pluviale_errore = None
    try:
        payload = fetch_previsione_pioggia(lat if lat is not None else FERRARA_LAT, lon if lon is not None else FERRARA_LON)
        pluviale = valuta_rischio_pluviale_locale(payload)
    except Exception as exc:
        pluviale_errore = f"Impossibile interrogare Open-Meteo per il rischio pluviale locale: {exc}"

    return {
        "sottobacino": sottobacino,
        "trend_livello_fiume": trend,
        "colmo_previsto_ufficiale": colmo_ufficiale,
        "rischio_pluviale_locale": pluviale,
        "rischio_pluviale_locale_errore": pluviale_errore,
    }


@app.get("/api/eventi")
def list_eventi(operatore: Operatore = Depends(get_current_operatore)):
    richiede_permesso(operatore, "vedi_eventi")
    db = SessionLocal()
    eventi = db.query(EventoIdraulico).order_by(EventoIdraulico.timestamp.desc()).all()
    result = [_evento_to_dict(e) for e in eventi]
    db.close()
    return result


@app.get("/api/eventi/{evento_id}/valutazioni")
def list_valutazioni(evento_id: int, operatore: Operatore = Depends(get_current_operatore)):
    richiede_permesso(operatore, "vedi_priorita")
    db = SessionLocal()
    evento = db.query(EventoIdraulico).filter(EventoIdraulico.id == evento_id).first()
    if not evento:
        db.close()
        raise HTTPException(status_code=404, detail="Lettura non trovata")
    valutazioni = (
        db.query(ValutazionePrioritaIdraulica)
        .filter(ValutazionePrioritaIdraulica.evento_id == evento_id)
        .order_by(ValutazionePrioritaIdraulica.priorita_rank.desc(), ValutazionePrioritaIdraulica.distanza_km.asc())
        .all()
    )
    result = {"evento": _evento_to_dict(evento), "valutazioni": [_valutazione_to_dict(v) for v in valutazioni]}
    db.close()
    return result


@app.get("/api/valutazioni/{valutazione_id}")
def get_valutazione(valutazione_id: int, operatore: Operatore = Depends(get_current_operatore)):
    richiede_permesso(operatore, "vedi_priorita")
    db = SessionLocal()
    v = db.query(ValutazionePrioritaIdraulica).filter(ValutazionePrioritaIdraulica.id == valutazione_id).first()
    if not v:
        db.close()
        raise HTTPException(status_code=404, detail="Valutazione non trovata")
    result = _valutazione_to_dict(v)
    db.close()
    return result


@app.get("/api/eventi/{evento_id}/riepilogo")
def get_riepilogo_evento(evento_id: int, operatore: Operatore = Depends(get_current_operatore)):
    richiede_permesso(operatore, "vedi_priorita")
    db = SessionLocal()
    evento = db.query(EventoIdraulico).filter(EventoIdraulico.id == evento_id).first()
    if not evento:
        db.close()
        raise HTTPException(status_code=404, detail="Lettura non trovata")
    valutazioni = db.query(ValutazionePrioritaIdraulica).filter(ValutazionePrioritaIdraulica.evento_id == evento_id).all()
    result = genera_riepilogo_proattivo(evento, valutazioni)
    db.close()
    return result


# --------------------------------------------------------------------------
# Logistica
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
# Assegnazioni risorse — sempre su conferma umana esplicita
# --------------------------------------------------------------------------

class AssegnazioneConfermaRequest(BaseModel):
    squadra_id: int
    structure_id: int
    evento_id: int | None = None
    note: str | None = None


@app.post("/api/assegnazioni/conferma")
def conferma_assegnazione(req: AssegnazioneConfermaRequest, operatore: Operatore = Depends(get_current_operatore)):
    """Unico endpoint che scrive un'assegnazione — vedi la stessa nota nel motore sismico: nessun percorso automatico arriva qui."""
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
# Vicky — query in linguaggio naturale, sola lettura (vedi app/vicky.py)
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
