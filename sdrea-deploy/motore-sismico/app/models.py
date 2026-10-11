"""
Modelli dati del motore sismico SDREA (Parte B della spec).
Database SQLite proprio, separato da quello di Ponti-Daghi-Edifici — vedi
README.md per il principio architetturale.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean, ForeignKey, JSON,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

PRIORITA_VALORI = ("bassa", "media", "alta", "critica")
RUOLI_VALORI = ("operatore_civile", "operatore_difesa", "amministratore")


class EventoSismico(Base):
    """B.2 — Evento sismico reale ingerito dal feed INGV."""
    __tablename__ = "eventi_sismici"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # id dell'evento lato INGV (campo "EventID" del formato FDSN text) — usato
    # per idempotenza: lo stesso evento non va ingerito due volte se il
    # poller lo rivede nel feed (finestra temporale sovrapposta tra un poll
    # e il successivo).
    ingv_event_id = Column(String, nullable=False, unique=True)
    magnitudo = Column(Float, nullable=False)
    lat_epicentro = Column(Float, nullable=False)
    lon_epicentro = Column(Float, nullable=False)
    profondita_km = Column(Float, nullable=True)
    timestamp = Column(DateTime, nullable=False)
    fonte = Column(String, nullable=False, default="INGV")
    luogo = Column(String, nullable=True)  # "EventLocationName" nel formato INGV, utile per la UI/Vicky
    processato_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    valutazioni = relationship("ValutazionePriorita", back_populates="evento", order_by="desc(ValutazionePriorita.priorita_rank)")


class ValutazionePriorita(Base):
    """
    B.3 — Priorità calcolata per una struttura in risposta a un evento
    sismico. Una riga per coppia (evento, struttura) — la struttura vive
    nel backend Ponti-Daghi-Edifici, qui si tiene solo l'id esterno
    (structure_id, quello del readiness-feed) e uno snapshot dei campi
    usati per il calcolo, così la spiegabilità (B.4.4) non dipende da una
    nuova chiamata HTTP a Ponti-Daghi-Edifici ogni volta che un operatore
    chiede "perché".
    """
    __tablename__ = "valutazioni_priorita"

    id = Column(Integer, primary_key=True, autoincrement=True)
    evento_id = Column(Integer, ForeignKey("eventi_sismici.id"), nullable=False)
    structure_id = Column(Integer, nullable=False)  # id esterno, lato Ponti-Daghi-Edifici
    structure_nome = Column(String, nullable=True)
    structure_lat = Column(Float, nullable=True)
    structure_lon = Column(Float, nullable=True)

    distanza_km = Column(Float, nullable=False)
    intensita_stimata = Column(Float, nullable=False)  # PGA stimata (g), vedi app/attenuation.py
    priorita_finale = Column(String, nullable=False)  # "bassa" | "media" | "alta" | "critica"
    # Per ordinare/filtrare per priorità senza dover mappare la stringa ogni volta.
    priorita_rank = Column(Integer, nullable=False)  # bassa=0, media=1, alta=2, critica=3

    # Snapshot dei fattori usati nel calcolo (B.4.4 — spiegabilità): mai
    # una "scatola chiusa", un operatore deve poter vedere esattamente
    # cosa ha contribuito alla priorità assegnata.
    fattori = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    evento = relationship("EventoSismico", back_populates="valutazioni")


class Operatore(Base):
    """
    B.5 — Utente autenticato del motore sismico (RBAC, vedi app/rbac.py
    per la nota sul rapporto con M8 della spec Defense).
    """
    __tablename__ = "operatori"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True)
    password_hash = Column(String, nullable=False)
    password_salt = Column(String, nullable=False)
    ruolo = Column(String, nullable=False, default="operatore_civile")
    attivo = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SessionToken(Base):
    __tablename__ = "session_tokens"

    token = Column(String, primary_key=True)
    operatore_id = Column(Integer, ForeignKey("operatori.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=False)


class SquadraLogistica(Base):
    """
    B.4.3 — Squadra/mezzo disponibile per il coordinamento risorse.

    Nota onesta sul "modulo Logistica M4": come per M8/RBAC (vedi
    app/rbac.py), in questa sessione non ho un servizio M4 condiviso da
    richiamare via rete — nella base di codice esistente M4 esiste finora
    solo lato client dentro SDREA.html (DefenseExtension). Questo modulo
    implementa un vero registro squadre/mezzi funzionante, con lo stesso
    contratto concettuale (risorsa con posizione, tipo, disponibilità,
    interrogabile per prossimità) così da poter essere sostituito da una
    chiamata al vero M4 in futuro senza toccare gli endpoint che lo usano
    — vedi app/logistica.py.
    """
    __tablename__ = "squadre_logistica"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String, nullable=False)
    tipo = Column(String, nullable=False)  # es. "squadra VVFF", "escavatore", "ambulanza", ...
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    disponibile = Column(Boolean, nullable=False, default=True)
    note = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AssegnazioneRisorsa(Base):
    """
    B.4.3 + vincolo esplicito B.4: registra l'ASSEGNAZIONE di una risorsa
    (squadra_id) a una struttura, sempre e solo come CONFERMA di una
    decisione umana — mai creata automaticamente dal motore di priorità.
    `confermata_da_id` è obbligatorio (FK a Operatore) proprio per
    rendere impossibile, per costruzione, un record "senza nessuno che
    abbia deciso": non esiste un percorso di codice in questo backend che
    crei un'AssegnazioneRisorsa senza un operatore autenticato che la
    conferma esplicitamente (vedi POST /api/assegnazioni/conferma in
    app/main.py — l'unico punto che scrive in questa tabella).
    """
    __tablename__ = "assegnazioni_risorsa"

    id = Column(Integer, primary_key=True, autoincrement=True)
    squadra_id = Column(Integer, ForeignKey("squadre_logistica.id"), nullable=False)
    structure_id = Column(Integer, nullable=False)  # id esterno, lato Ponti-Daghi-Edifici
    evento_id = Column(Integer, ForeignKey("eventi_sismici.id"), nullable=True)
    confermata_da_id = Column(Integer, ForeignKey("operatori.id"), nullable=False)
    note = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
