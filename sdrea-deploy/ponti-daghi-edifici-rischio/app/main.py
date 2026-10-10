"""
Backend "Ponti, Daghi e Edifici a Rischio".

Avvio locale:
    pip install -r requirements.txt
    uvicorn app.main:app --reload

Poi apri http://localhost:8000

Endpoint chiave per l'aggancio a SDREA (modulo M5 - Readiness Infrastrutture
Critiche, e motore sismico Parte B della spec "SDREA + Ponti, Daghi e
Edifici a Rischio"):
    GET  /api/sdrea/readiness-feed
    Restituisce, per ogni struttura, colore di readiness + coordinate +
    i campi di vulnerabilità/esposizione (A.8): esattamente il formato che
    SDREA consuma per l'overlay sulla mappa, gli alert e il motore di
    priorità combinata (B.3).

    POST /api/sdrea/eventi
    Riceve da SDREA un evento reale (sisma, ecc.) che coinvolge una o più
    strutture — popola la tabella EventoStruttura (A.4). Vedi Fase 5.
"""

import os
import shutil
import uuid
from datetime import date, datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Structure, Inspection, Ispettore, SessionToken, MisurazioneEGMS, EventoStruttura,
    TIPI_STRUTTURA, TIPOLOGIE_STRUTTURALI, RIDONDANZA_VALORI,
)
from app.migrations import run_migrations
from app.auth import hash_password, verify_password, new_token, make_auth_dependency, make_service_auth_dependency
from app.integrity import compute_inspection_hash
from app.egms import compute_trend

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Database — 2026-10-10: DATABASE_URL opzionale per un disco persistente
# gratuito (es. Neon, Postgres serverless: storage separato dal compute,
# i dati sopravvivono ai riavvii del container — a differenza del file
# SQLite locale su Render free, vedi CONSEGNA.md limitazione nota #5,
# quella che riguarda proprio questo servizio in origine). Se
# DATABASE_URL non è impostata, il comportamento resta ESATTAMENTE
# quello di sempre: file SQLite locale, non persistente tra riavvii —
# questa è un'aggiunta facoltativa, non una sostituzione imposta.
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    engine = create_engine(DATABASE_URL)
else:
    DB_PATH = os.path.join(os.path.dirname(__file__), "..", "ponti_daghi.db")
    engine = create_engine(f"sqlite:///{DB_PATH}")
Base.metadata.create_all(engine)
run_migrations(engine)  # additiva, non distruttiva — vedi app/migrations.py
SessionLocal = sessionmaker(bind=engine)

app = FastAPI(title="Ponti, Daghi e Edifici a Rischio")

# CORS permissivo — 2026-10-10: senza questo, il browser blocca dal
# frontend (index.html, pannello "Ponti, Daghi ed Edifici a Rischio")
# qualunque chiamata a questa API, e il sito la vede come "connessione
# di rete non riuscita" anche quando il servizio è sveglio e raggiungibile.
# Stesso schema già in uso su motore-idraulico/motore-sismico. Da
# restringere a un'origine precisa prima di qualunque esposizione oltre
# la rete di sviluppo/demo.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# A.5 — dependency FastAPI che risolve il bearer token nell'header
# Authorization nell'Ispettore autenticato, o alza 401.
get_current_ispettore = make_auth_dependency(SessionLocal, Ispettore, SessionToken)

# Canale server-to-server con SDREA (motore sismico): chiave condivisa in
# header, vedi app/auth.py:make_service_auth_dependency per il comportamento
# esatto (aperto finché SDREA_SERVICE_KEY non è impostata).
verify_service_key = make_service_auth_dependency()


# ---------------------------------------------------------------------------
# Schemi richiesta
# ---------------------------------------------------------------------------

class StructureCreate(BaseModel):
    name: str
    tipo: str  # "ponte" | "diga" | "edificio"
    comune: str = ""
    lat: float | None = None
    lon: float | None = None
    anno_costruzione: int | None = None

    # A.1 — solo per tipo "edificio"
    numero_piani: int | None = None
    destinazione_uso: str | None = None

    # A.2 — Vulnerabilità. ridondanza e normativa_antisismica sono dati
    # autorevoli: chi chiama questa API deve essere un tecnico qualificato
    # (o un import da documentazione ufficiale), il sistema non li deduce.
    tipologia_strutturale: str | None = None
    ridondanza: str | None = None
    zona_sismica: int | None = None
    normativa_antisismica: bool | None = None
    ultima_verifica_tecnica: date | None = None

    # A.3 — Esposizione
    traffico_giornaliero_stimato: int | None = None
    percorsi_alternativi: bool | None = None
    edifici_sensibili_vicini: int | None = None
    popolazione_servita_stimata: int | None = None

    # A.6 — idempotenza per creazioni fatte offline e poi ritentate.
    client_uuid: str | None = None

    @field_validator("tipo")
    @classmethod
    def _valida_tipo(cls, v):
        if v not in TIPI_STRUTTURA:
            raise ValueError(f"tipo deve essere uno tra {TIPI_STRUTTURA}")
        return v

    @field_validator("tipologia_strutturale")
    @classmethod
    def _valida_tipologia(cls, v):
        if v is not None and v not in TIPOLOGIE_STRUTTURALI:
            raise ValueError(f"tipologia_strutturale deve essere una tra {TIPOLOGIE_STRUTTURALI}")
        return v

    @field_validator("ridondanza")
    @classmethod
    def _valida_ridondanza(cls, v):
        if v is not None and v not in RIDONDANZA_VALORI:
            raise ValueError(f"ridondanza deve essere una tra {RIDONDANZA_VALORI}")
        return v

    @field_validator("zona_sismica")
    @classmethod
    def _valida_zona_sismica(cls, v):
        if v is not None and v not in (1, 2, 3, 4):
            raise ValueError("zona_sismica deve essere un intero tra 1 e 4")
        return v


class StructureUpdate(BaseModel):
    """
    Aggiornamento parziale di una struttura esistente — pensato in
    particolare per i campi di Vulnerabilità (A.2), tipicamente compilati
    da un tecnico qualificato DOPO la creazione della scheda struttura,
    non necessariamente al momento del censimento iniziale.
    Stessa validazione di StructureCreate sui campi presenti.
    """
    name: str | None = None
    comune: str | None = None
    lat: float | None = None
    lon: float | None = None
    anno_costruzione: int | None = None
    numero_piani: int | None = None
    destinazione_uso: str | None = None
    tipologia_strutturale: str | None = None
    ridondanza: str | None = None
    zona_sismica: int | None = None
    normativa_antisismica: bool | None = None
    ultima_verifica_tecnica: date | None = None
    traffico_giornaliero_stimato: int | None = None
    percorsi_alternativi: bool | None = None
    edifici_sensibili_vicini: int | None = None
    popolazione_servita_stimata: int | None = None

    # A.6 — rilevamento conflitti. Il client invia l'updated_at che
    # conosceva al momento in cui ha preparato la modifica (tipicamente
    # offline): se al momento della sincronizzazione non coincide più con
    # quello reale sul server, vuol dire che qualcun altro ha modificato
    # la struttura nel frattempo — il server rifiuta con 409 invece di
    # sovrascrivere silenziosamente la modifica altrui.
    base_updated_at: datetime | None = None

    @field_validator("tipologia_strutturale")
    @classmethod
    def _valida_tipologia(cls, v):
        if v is not None and v not in TIPOLOGIE_STRUTTURALI:
            raise ValueError(f"tipologia_strutturale deve essere una tra {TIPOLOGIE_STRUTTURALI}")
        return v

    @field_validator("ridondanza")
    @classmethod
    def _valida_ridondanza(cls, v):
        if v is not None and v not in RIDONDANZA_VALORI:
            raise ValueError(f"ridondanza deve essere una tra {RIDONDANZA_VALORI}")
        return v

    @field_validator("zona_sismica")
    @classmethod
    def _valida_zona_sismica(cls, v):
        if v is not None and v not in (1, 2, 3, 4):
            raise ValueError("zona_sismica deve essere un intero tra 1 e 4")
        return v


class IspettoreRegister(BaseModel):
    nome: str
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _valida_email(cls, v):
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("email non valida")
        return v.strip().lower()

    @field_validator("password")
    @classmethod
    def _valida_password(cls, v):
        if len(v) < 8:
            raise ValueError("la password deve avere almeno 8 caratteri")
        return v


class IspettoreLogin(BaseModel):
    email: str
    password: str


# ---------------------------------------------------------------------------
# Stima preliminare SDREA — SOLO normativa_antisismica (2026-10-06)
# ---------------------------------------------------------------------------

def _stima_preliminare_normativa_antisismica(anno_costruzione: int | None) -> tuple[bool | None, str | None]:
    """
    Stima preliminare, esplicitamente NON una verifica tecnica, di
    conformità alla normativa antisismica — basata solo sull'anno di
    costruzione, secondo quando l'obbligo di progettazione antisismica si
    è esteso in Italia. Scritta su un campo separato
    (normativa_antisismica_stima_preliminare), MAI su normativa_antisismica
    (il campo confermato da tecnico): vedi il commento in app/models.py per
    il perché di questa separazione, e per il perché lo stesso approccio
    NON viene applicato a `ridondanza`.

    - Prima del 1975 (Legge 64/1974, primo riferimento normativo nazionale
      organico sulla progettazione antisismica, entrata in vigore nel
      1975): probabilmente NO.
    - Dal 2009 in poi (le NTC 2008 sono obbligatorie dal 1° luglio 2009 su
      tutto il territorio nazionale, incluse le zone a bassa sismicità
      prima non normate): probabilmente SÌ.
    - Fra il 1975 e il 2008 incluso: NESSUNA stima — dipende da quando la
      zona sismica SPECIFICA della struttura è stata classificata (la
      riclassificazione OPCM 3274/2003 ha esteso l'obbligo a molte zone
      prima non normate, con tempi diversi comune per comune), troppo
      incerto per essere stimato solo dall'anno.

    Ritorna (None, None) se anno_costruzione non è noto — mai un sì/no
    forzato in assenza di dato.
    """
    if anno_costruzione is None:
        return None, None
    if anno_costruzione < 1975:
        return False, (
            f"Stima preliminare SDREA (NON verificata): costruita nel {anno_costruzione}, prima "
            "dell'entrata in vigore della Legge 64/1974 (1975), primo riferimento normativo "
            "nazionale organico sulla progettazione antisismica — probabilmente NON conforme. "
            "Da confermare con una verifica tecnica diretta da parte di un tecnico qualificato."
        )
    if anno_costruzione >= 2009:
        return True, (
            f"Stima preliminare SDREA (NON verificata): costruita nel {anno_costruzione}, dopo "
            "l'entrata in vigore delle Norme Tecniche per le Costruzioni 2008 (NTC 2008, "
            "obbligatorie dal 1° luglio 2009 su tutto il territorio nazionale) — probabilmente "
            "conforme. Da confermare con una verifica tecnica diretta da parte di un tecnico "
            "qualificato."
        )
    return None, (
        f"Nessuna stima preliminare affidabile: costruita nel {anno_costruzione}, in una fascia "
        "temporale (1975-2008) in cui l'obbligo dipende da quando la zona sismica specifica di "
        "questa struttura è stata classificata — richiede verifica tecnica diretta da parte di "
        "un tecnico qualificato."
    )


# ---------------------------------------------------------------------------
# Helper di serializzazione
# ---------------------------------------------------------------------------

def _structure_to_dict(s: Structure, db=None) -> dict:
    r = s.readiness
    return {
        "id": s.id,
        "name": s.name,
        "tipo": s.tipo,
        "comune": s.comune,
        "lat": s.lat,
        "lon": s.lon,
        "anno_costruzione": s.anno_costruzione,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        "numero_piani": s.numero_piani,
        "destinazione_uso": s.destinazione_uso,
        "tipologia_strutturale": s.tipologia_strutturale,
        "ridondanza": s.ridondanza,
        "zona_sismica": s.zona_sismica,
        "normativa_antisismica": s.normativa_antisismica,
        "normativa_antisismica_stima_preliminare": s.normativa_antisismica_stima_preliminare,
        "normativa_antisismica_stima_motivazione": s.normativa_antisismica_stima_motivazione,
        "ultima_verifica_tecnica": s.ultima_verifica_tecnica.isoformat() if s.ultima_verifica_tecnica else None,
        "traffico_giornaliero_stimato": s.traffico_giornaliero_stimato,
        "percorsi_alternativi": s.percorsi_alternativi,
        "edifici_sensibili_vicini": s.edifici_sensibili_vicini,
        "popolazione_servita_stimata": s.popolazione_servita_stimata,
        "readiness_colore": r["colore"],
        "readiness_motivo": r["motivo"],
        "n_ispezioni": len(s.inspections),
        "ultima_ispezione": s.inspections[0].data.isoformat() if s.inspections else None,
        "trend_deformazione_egms": _egms_trend_label(s.id, db) if db is not None else None,
    }


def _inspection_to_dict(i: Inspection) -> dict:
    return {
        "id": i.id,
        "structure_id": i.structure_id,
        "data": i.data.isoformat() if i.data else None,
        "ispettore": i.ispettore,  # nome al momento della creazione, sola visualizzazione
        "ispettore_id": i.ispettore_id,
        "gravita": i.gravita,
        "note": i.note,
        "foto_path": i.foto_path,
        "hash_verifica": i.hash_verifica,
    }


# ---------------------------------------------------------------------------
# Rotte
# ---------------------------------------------------------------------------

@app.get("/")
def serve_dashboard():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ---------------------------------------------------------------------------
# A.5 — Autenticazione ispettori
# ---------------------------------------------------------------------------
# Nessun pannello di amministrazione in questo MVP: la registrazione è
# aperta (chiunque può crearsi un account ispettore). Prima di un uso
# reale sul campo, chi gestisce il progetto deve decidere se e come
# restringerla (es. invito da un amministratore, dominio email aziendale
# consentito) — deliberatamente lasciato aperto qui per non bloccare i
# test, ma NON è la postura giusta per la produzione senza una decisione
# esplicita in merito.

@app.post("/api/auth/register")
def register(req: IspettoreRegister):
    db = SessionLocal()
    existing = db.query(Ispettore).filter(Ispettore.email == req.email).first()
    if existing:
        db.close()
        raise HTTPException(status_code=400, detail="Email già registrata")
    password_hash, salt = hash_password(req.password)
    ispettore = Ispettore(nome=req.nome, email=req.email, password_hash=password_hash, password_salt=salt)
    db.add(ispettore)
    db.commit()
    db.refresh(ispettore)
    token, expires_at = new_token()
    db.add(SessionToken(token=token, ispettore_id=ispettore.id, expires_at=expires_at))
    db.commit()
    result = {"token": token, "ispettore_id": ispettore.id, "nome": ispettore.nome}
    db.close()
    return result


@app.post("/api/auth/login")
def login(req: IspettoreLogin):
    db = SessionLocal()
    ispettore = db.query(Ispettore).filter(Ispettore.email == req.email.strip().lower()).first()
    if not ispettore or not ispettore.attivo or not verify_password(req.password, ispettore.password_hash, ispettore.password_salt):
        db.close()
        raise HTTPException(status_code=401, detail="Email o password non corretti")
    token, expires_at = new_token()
    db.add(SessionToken(token=token, ispettore_id=ispettore.id, expires_at=expires_at))
    db.commit()
    result = {"token": token, "ispettore_id": ispettore.id, "nome": ispettore.nome}
    db.close()
    return result


@app.get("/api/auth/me")
def me(ispettore: Ispettore = Depends(get_current_ispettore)):
    return {"id": ispettore.id, "nome": ispettore.nome, "email": ispettore.email}


@app.get("/api/structures")
def list_structures():
    db = SessionLocal()
    structures = db.query(Structure).order_by(Structure.id.desc()).all()
    result = [_structure_to_dict(s, db) for s in structures]
    db.close()
    return result


@app.get("/api/structures/{structure_id}")
def get_structure(structure_id: int):
    db = SessionLocal()
    s = db.query(Structure).filter(Structure.id == structure_id).first()
    if not s:
        db.close()
        raise HTTPException(status_code=404, detail="Struttura non trovata")
    result = _structure_to_dict(s, db)
    db.close()
    return result


@app.post("/api/structures")
def create_structure(req: StructureCreate):
    db = SessionLocal()
    if req.client_uuid:
        existing = db.query(Structure).filter(Structure.client_uuid == req.client_uuid).first()
        if existing:
            result = _structure_to_dict(existing, db)
            result["duplicate"] = True
            db.close()
            return result
    s = Structure(**req.model_dump())
    # Stima preliminare SDREA (solo normativa_antisismica, vedi funzione
    # dedicata sopra) — calcolata qui, mai presa in input dal client: non è
    # un dato che chi registra la struttura dichiara, è un'inferenza del
    # sistema da anno_costruzione, sempre ricalcolabile e mai autorevole.
    s.normativa_antisismica_stima_preliminare, s.normativa_antisismica_stima_motivazione = (
        _stima_preliminare_normativa_antisismica(s.anno_costruzione)
    )
    if s.normativa_antisismica_stima_motivazione is not None:
        s.normativa_antisismica_stima_creata_il = datetime.now(timezone.utc)
    db.add(s)
    db.commit()
    db.refresh(s)
    result = _structure_to_dict(s, db)
    db.close()
    return result


@app.patch("/api/structures/{structure_id}")
def update_structure(structure_id: int, req: StructureUpdate):
    db = SessionLocal()
    s = db.query(Structure).filter(Structure.id == structure_id).first()
    if not s:
        db.close()
        raise HTTPException(status_code=404, detail="Struttura non trovata")

    # A.6 — rilevamento conflitti: se il client ha dichiarato da quale
    # updated_at partiva (tipicamente una modifica preparata offline) e
    # non coincide più con quello reale, qualcun altro ha modificato la
    # struttura nel frattempo. Rifiutiamo con 409 e restituiamo lo stato
    # attuale, invece di sovrascriverlo silenziosamente — la risoluzione
    # (tenere la mia modifica, scartarla, o unire a mano) spetta a chi
    # sincronizza, mai a un last-write-wins automatico e silenzioso.
    payload = req.model_dump(exclude_unset=True)
    base_updated_at = payload.pop("base_updated_at", None)
    if base_updated_at is not None and s.updated_at is not None:
        # Confronto al secondo: evita falsi conflitti per differenze di
        # microsecondi introdotte da serializzazione/deserializzazione JSON.
        if int(base_updated_at.timestamp()) != int(s.updated_at.timestamp()):
            conflict_detail = {
                "message": "La struttura è stata modificata da un altro utente dopo che questa modifica è stata preparata.",
                "struttura_attuale": _structure_to_dict(s, db),  # serializzato PRIMA di chiudere la sessione, altrimenti il lazy-load di s.inspections fallisce
            }
            db.close()
            raise HTTPException(status_code=409, detail=conflict_detail)

    for field, value in payload.items():
        setattr(s, field, value)
    if "anno_costruzione" in payload:
        # Ricalcola la stima preliminare solo se l'anno di costruzione è
        # stato toccato in questo aggiornamento — mai su ogni PATCH, per
        # non sovrascrivere inutilmente normativa_antisismica_stima_creata_il
        # quando si aggiorna, ad esempio, solo il traffico stimato.
        s.normativa_antisismica_stima_preliminare, s.normativa_antisismica_stima_motivazione = (
            _stima_preliminare_normativa_antisismica(s.anno_costruzione)
        )
        s.normativa_antisismica_stima_creata_il = (
            datetime.now(timezone.utc) if s.normativa_antisismica_stima_motivazione is not None else None
        )
    db.commit()
    db.refresh(s)
    result = _structure_to_dict(s, db)
    db.close()
    return result


@app.post("/api/structures/{structure_id}/inspections")
async def create_inspection(
    structure_id: int,
    gravita: str = Form(...),
    note: str = Form(""),
    foto: UploadFile | None = File(None),
    client_uuid: str | None = Form(None),
    ispettore: Ispettore = Depends(get_current_ispettore),
):
    # A.5 — l'identità dell'ispettore non è più un campo di testo che il
    # client può scrivere a piacere: è quella dell'utente autenticato che
    # ha fatto questa richiesta (risolta da get_current_ispettore dal
    # bearer token). Sostituisce il vecchio parametro `ispettore: str`.
    if gravita not in ("bassa", "media", "alta"):
        raise HTTPException(status_code=400, detail="gravita deve essere 'bassa', 'media' o 'alta'")

    db = SessionLocal()
    structure = db.query(Structure).filter(Structure.id == structure_id).first()
    if not structure:
        db.close()
        raise HTTPException(status_code=404, detail="Struttura non trovata")

    # A.6 — idempotenza: se questa stessa ispezione (stesso client_uuid,
    # generato dal client quando l'ha accodata offline) è già stata
    # sincronizzata con successo in un tentativo precedente la cui
    # risposta è andata persa, non creiamo un duplicato.
    if client_uuid:
        existing = db.query(Inspection).filter(Inspection.client_uuid == client_uuid).first()
        if existing:
            result = {"id": existing.id, "structure_id": structure_id, "readiness": structure.readiness, "duplicate": True}
            db.close()
            return result

    foto_path = None
    if foto is not None:
        ext = os.path.splitext(foto.filename)[1] or ".jpg"
        fname = f"{uuid.uuid4().hex}{ext}"
        dest = os.path.join(UPLOAD_DIR, fname)
        with open(dest, "wb") as buffer:
            shutil.copyfileobj(foto.file, buffer)
        foto_path = f"/static/uploads/{fname}"

    data_ts = datetime.now(timezone.utc)
    # A.5 — hash di integrità calcolato dal SERVER (mai dal client: un
    # client compromesso o in errore potrebbe altrimenti inviare un hash
    # che non riflette davvero il contenuto) sul contenuto significativo
    # del record, subito prima di scriverlo — vedi app/integrity.py.
    # NB: SQLite non conserva il fuso orario di un DateTime — quando il
    # valore viene riletto dal DB torna "naive" (senza +00:00), anche se
    # è stato scritto con tzinfo. Per poter ricalcolare lo stesso hash in
    # /verify dopo un giro DB, l'hash va calcolato sulla stessa
    # rappresentazione naive che si otterrà rileggendo il record, non su
    # quella tz-aware di adesso (altrimenti l'hash non coinciderebbe mai,
    # nemmeno per un record mai alterato).
    data_iso_per_hash = data_ts.replace(tzinfo=None).isoformat()
    hash_verifica = compute_inspection_hash(
        structure_id=structure_id, data_iso=data_iso_per_hash, ispettore_id=ispettore.id,
        gravita=gravita, note=note, foto_path=foto_path,
    )

    inspection = Inspection(
        structure_id=structure_id, data=data_ts,
        ispettore=ispettore.nome, ispettore_id=ispettore.id,
        gravita=gravita, note=note, foto_path=foto_path,
        client_uuid=client_uuid, hash_verifica=hash_verifica,
    )
    db.add(inspection)
    db.commit()
    db.refresh(inspection)
    result = {"id": inspection.id, "structure_id": structure_id, "readiness": structure.readiness}
    db.close()
    return result


@app.get("/api/structures/{structure_id}/inspections")
def list_inspections(structure_id: int):
    db = SessionLocal()
    inspections = (
        db.query(Inspection)
        .filter(Inspection.structure_id == structure_id)
        .order_by(Inspection.data.desc())
        .all()
    )
    result = [_inspection_to_dict(i) for i in inspections]
    db.close()
    return result


@app.get("/api/inspections/{inspection_id}/verify")
def verify_inspection(inspection_id: int):
    """
    A.5 — Ricalcola l'hash dai valori CORRENTI del record e lo confronta
    con quello salvato al momento della creazione. Se non coincidono, il
    record è stato alterato dopo la creazione da qualcosa che non è
    passato dall'endpoint di creazione (che è l'unico che scrive
    hash_verifica) — es. un update diretto sul database.
    """
    db = SessionLocal()
    i = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    if not i:
        db.close()
        raise HTTPException(status_code=404, detail="Ispezione non trovata")
    recomputed = compute_inspection_hash(
        structure_id=i.structure_id, data_iso=i.data.isoformat() if i.data else "",
        ispettore_id=i.ispettore_id, gravita=i.gravita, note=i.note, foto_path=i.foto_path,
    )
    result = {
        "id": i.id,
        "hash_registrato": i.hash_verifica,
        "hash_ricalcolato": recomputed,
        "integro": (i.hash_verifica == recomputed) if i.hash_verifica else None,
    }
    db.close()
    return result


class EGMSPuntoImport(BaseModel):
    data: date
    spostamento_cumulato_mm: float


class EGMSImportRequest(BaseModel):
    punti: list[EGMSPuntoImport]
    fonte: str | None = None


@app.post("/api/structures/{structure_id}/egms/import")
def import_egms_points(
    structure_id: int,
    req: EGMSImportRequest,
    ispettore: Ispettore = Depends(get_current_ispettore),
):
    """
    A.7 — Importa una serie storica di deformazione del terreno per una
    struttura, esportata dall'EGMS Explorer (CSV/JSON convertito lato
    client in {data, spostamento_cumulato_mm}). Richiede autenticazione
    per la stessa ragione di A.5 sulle ispezioni: sapere chi ha importato
    un dato che influenza il readiness-feed e potenzialmente il motore di
    priorità di SDREA (B.3) è responsabilità professionale, non solo
    tracciabilità formale.
    """
    db = SessionLocal()
    s = db.query(Structure).filter(Structure.id == structure_id).first()
    if not s:
        db.close()
        raise HTTPException(status_code=404, detail="Struttura non trovata")
    if not req.punti:
        db.close()
        raise HTTPException(status_code=400, detail="Nessun punto fornito")

    fonte = req.fonte or "EGMS Explorer (import manuale)"
    for p in req.punti:
        db.add(MisurazioneEGMS(
            structure_id=structure_id,
            data=p.data,
            spostamento_cumulato_mm=p.spostamento_cumulato_mm,
            fonte=fonte,
            importato_da_id=ispettore.id,
        ))
    db.commit()
    n_totale = db.query(MisurazioneEGMS).filter(MisurazioneEGMS.structure_id == structure_id).count()
    db.close()
    return {"structure_id": structure_id, "punti_importati": len(req.punti), "punti_totali": n_totale}


@app.get("/api/structures/{structure_id}/egms/points")
def list_egms_points(structure_id: int):
    db = SessionLocal()
    punti = (
        db.query(MisurazioneEGMS)
        .filter(MisurazioneEGMS.structure_id == structure_id)
        .order_by(MisurazioneEGMS.data)
        .all()
    )
    result = [
        {
            "id": p.id,
            "data": p.data.isoformat(),
            "spostamento_cumulato_mm": p.spostamento_cumulato_mm,
            "fonte": p.fonte,
        }
        for p in punti
    ]
    db.close()
    return result


@app.get("/api/structures/{structure_id}/egms/trend")
def get_egms_trend(structure_id: int):
    db = SessionLocal()
    s = db.query(Structure).filter(Structure.id == structure_id).first()
    if not s:
        db.close()
        raise HTTPException(status_code=404, detail="Struttura non trovata")
    punti = (
        db.query(MisurazioneEGMS)
        .filter(MisurazioneEGMS.structure_id == structure_id)
        .order_by(MisurazioneEGMS.data)
        .all()
    )
    trend = compute_trend([{"data": p.data, "spostamento_cumulato_mm": p.spostamento_cumulato_mm} for p in punti])
    db.close()
    return trend


def _egms_trend_label(structure_id: int, db) -> str | None:
    """
    Usato dal readiness-feed: ritorna solo l'etichetta testuale del trend
    (formato A.8: "in accelerazione", non l'intero oggetto di dettaglio),
    o None se non ci sono ancora abbastanza dati importati per quella
    struttura — mai un valore inventato in assenza di dati.
    """
    punti = (
        db.query(MisurazioneEGMS)
        .filter(MisurazioneEGMS.structure_id == structure_id)
        .order_by(MisurazioneEGMS.data)
        .all()
    )
    if len(punti) < 3:
        return None
    trend = compute_trend([{"data": p.data, "spostamento_cumulato_mm": p.spostamento_cumulato_mm} for p in punti])
    return trend["descrizione"]


class EventoStrutturaCreate(BaseModel):
    structure_id: int
    tipo_evento: str
    data: datetime
    intensita_stimata: float | None = None
    fonte: str | None = None


@app.post("/api/sdrea/eventi")
def ricevi_evento_struttura(req: EventoStrutturaCreate, _=Depends(verify_service_key)):
    """
    A.4 — Webhook: riceve da SDREA (Fase 5, motore sismico B.2/B.3) un
    evento reale che coinvolge questa struttura, e lo salva in
    EventoStruttura. Nessuna autenticazione utente qui (non è un'azione
    umana come un'ispezione): è un canale server-to-server tra i due
    backend, protetto da una chiave condivisa in header (vedi
    app/auth.py:make_service_auth_dependency) impostata via variabile
    d'ambiente SDREA_SERVICE_KEY su entrambi i servizi.
    intensita_stimata arriva da SDREA (stimata lì col modello di
    attenuazione, vedi la nota su A.4/EventoStruttura in app/models.py) —
    non viene mai ricalcolata o corretta qui.
    """
    db = SessionLocal()
    s = db.query(Structure).filter(Structure.id == req.structure_id).first()
    if not s:
        db.close()
        raise HTTPException(status_code=404, detail="Struttura non trovata")
    evento = EventoStruttura(
        structure_id=req.structure_id,
        tipo_evento=req.tipo_evento,
        data=req.data,
        intensita_stimata=req.intensita_stimata,
        fonte=req.fonte or "SDREA",
    )
    db.add(evento)
    db.commit()
    db.refresh(evento)
    result = {
        "id": evento.id,
        "structure_id": evento.structure_id,
        "tipo_evento": evento.tipo_evento,
        "data": evento.data.isoformat(),
        "intensita_stimata": evento.intensita_stimata,
        "fonte": evento.fonte,
    }
    db.close()
    return result


@app.get("/api/sdrea/readiness-feed")
def sdrea_readiness_feed(_=Depends(verify_service_key)):
    """
    Formato A.8 della spec: una struttura per elemento, con colore di
    readiness, coordinate e i campi di vulnerabilità/esposizione che
    alimentano il motore di priorità combinata di SDREA (B.3). Stesso
    canale protetto di /api/sdrea/eventi (vedi sopra).

    trend_deformazione_egms (A.7): calcolato dai punti EGMS importati per
    la struttura (vedi /api/structures/{id}/egms/import); resta null
    finché per quella struttura non ci sono almeno 3 punti storici — non
    è mai un valore inventato in assenza di dati sufficienti.
    """
    db = SessionLocal()
    structures = db.query(Structure).all()
    feed = []
    for s in structures:
        if s.lat is None or s.lon is None:
            continue  # SDREA ha bisogno di coordinate per mostrarlo sulla mappa
        r = s.readiness
        feed.append({
            "id": f"ponti-daghi-{s.id}",
            "nome": s.name,
            "tipo": s.tipo,
            "lat": s.lat,
            "lon": s.lon,
            "readiness": r["colore"],
            "motivo": r["motivo"],
            "tipologia_strutturale": s.tipologia_strutturale,
            "ridondanza": s.ridondanza,
            "zona_sismica": s.zona_sismica,
            "normativa_antisismica": s.normativa_antisismica,
            "normativa_antisismica_stima_preliminare": s.normativa_antisismica_stima_preliminare,
            "normativa_antisismica_stima_motivazione": s.normativa_antisismica_stima_motivazione,
            "traffico_giornaliero_stimato": s.traffico_giornaliero_stimato,
            "percorsi_alternativi": s.percorsi_alternativi,
            "edifici_sensibili_vicini": s.edifici_sensibili_vicini,
            "trend_deformazione_egms": _egms_trend_label(s.id, db),
            "ultimo_aggiornamento": datetime.utcnow().isoformat(),
        })
    db.close()
    return {"source": "ispezione-ponti-daghi", "generated_at": datetime.utcnow().isoformat(), "strutture": feed}
