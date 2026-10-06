"""
Modelli dati: Strutture (ponti/dighe/edifici) e Ispezioni.
Il punteggio di rischio è calcolato con una regola trasparente e modificabile
(tempo dall'ultima ispezione + gravità auto-dichiarata dall'ispettore),
NON da un modello di visione che giudica in autonomia lo stato strutturale.

Estensione A.1/A.2/A.3 (spec "SDREA + Ponti, Daghi e Edifici a Rischio" v1.0):
- A.1: tipo ampliato a "ponte" | "diga" | "edificio", con campi specifici per
  edificio (numero_piani, destinazione_uso).
- A.2: campi di Vulnerabilità — pensati esplicitamente per intercettare il
  tipo di fragilità strutturale che ha causato il collasso del Ponte
  Morandi (assenza di ridondanza non rilevabile da ispezione visiva
  ordinaria). IMPORTANTE: ridondanza e normativa_antisismica sono dati
  autorevoli inseriti da un tecnico qualificato (o importati da
  documentazione ufficiale) — il sistema non li calcola né li deduce mai
  automaticamente.
- A.3: campi di Esposizione (traffico, percorsi alternativi, edifici
  sensibili vicini, popolazione servita).
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Date, Text, Boolean, ForeignKey,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

TIPI_STRUTTURA = ("ponte", "diga", "edificio")
TIPOLOGIE_STRUTTURALI = ("trave", "arco", "strallato", "sospeso", "a_cassone", "altro")
RIDONDANZA_VALORI = ("ridondante", "non_ridondante", "da_verificare")


class Structure(Base):
    __tablename__ = "structures"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    tipo = Column(String, nullable=False)  # "ponte" | "diga" | "edificio"
    comune = Column(String, nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    anno_costruzione = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # A.6 — usato per il rilevamento di conflitti quando una modifica fatta
    # offline viene sincronizzata: il client invia insieme alla modifica
    # l'updated_at che conosceva, e se non coincide più con quello reale
    # (perché qualcun altro ha modificato la struttura nel frattempo) il
    # server rifiuta con 409 invece di sovrascrivere silenziosamente.
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    # A.6 — idempotenza: come Inspection.client_uuid, per creazioni di
    # struttura fatte offline e ritentate dopo un timeout di rete.
    client_uuid = Column(String, nullable=True)

    # --- A.1: campi specifici per tipo "edificio" (nulli per ponte/diga) ---
    numero_piani = Column(Integer, nullable=True)
    destinazione_uso = Column(String, nullable=True)  # "scuola" | "ospedale" | "residenziale" | altro

    # --- A.2: Vulnerabilità ---
    # ridondanza e normativa_antisismica NON vengono MAI impostati da codice
    # o da un modello automatico: vanno compilati da un tecnico qualificato
    # o importati da documentazione ufficiale della struttura (vedi nota
    # nella spec, A.2). Il default "da_verificare" riflette l'assenza di
    # un dato certo, non una valutazione.
    tipologia_strutturale = Column(String, nullable=True)
    ridondanza = Column(String, nullable=True, default="da_verificare")
    zona_sismica = Column(Integer, nullable=True)  # 1-4, classificazione sismica nazionale
    normativa_antisismica = Column(Boolean, nullable=True)
    ultima_verifica_tecnica = Column(Date, nullable=True)  # verifica strutturale formale, distinta dall'ispezione visiva

    # --- A.3: Esposizione ---
    traffico_giornaliero_stimato = Column(Integer, nullable=True)
    percorsi_alternativi = Column(Boolean, nullable=True)
    edifici_sensibili_vicini = Column(Integer, nullable=True)  # scuole/ospedali entro raggio configurabile
    popolazione_servita_stimata = Column(Integer, nullable=True)

    inspections = relationship("Inspection", back_populates="structure", order_by="desc(Inspection.data)")
    eventi = relationship("EventoStruttura", back_populates="structure", order_by="desc(EventoStruttura.data)")
    misurazioni_egms = relationship("MisurazioneEGMS", back_populates="structure", order_by="MisurazioneEGMS.data")

    @property
    def readiness(self) -> dict:
        """
        Calcola lo stato di readiness (verde/giallo/rosso) dall'ultima ispezione.
        Regola trasparente, non un giudizio automatico di sicurezza:
        - Nessuna ispezione mai registrata -> rosso (dato mancante = priorità massima)
        - Ultima ispezione con gravità "alta" -> rosso
        - Ultima ispezione con gravità "media" O più di 365 giorni fa -> giallo
        - Altrimenti -> verde
        """
        if not self.inspections:
            return {"colore": "rosso", "motivo": "Nessuna ispezione mai registrata"}

        ultima = self.inspections[0]
        giorni_da_ultima = (datetime.now(timezone.utc) - ultima.data.replace(tzinfo=timezone.utc)).days

        if ultima.gravita == "alta":
            return {"colore": "rosso", "motivo": f"Ultima ispezione ({ultima.data.date()}) ha rilevato gravità alta"}
        if ultima.gravita == "media" or giorni_da_ultima > 365:
            motivo = "Gravità media rilevata" if ultima.gravita == "media" else f"Ultima ispezione {giorni_da_ultima} giorni fa (>365)"
            return {"colore": "giallo", "motivo": motivo}
        return {"colore": "verde", "motivo": f"Ultima ispezione ({ultima.data.date()}) senza criticità, entro i 365 giorni"}


class Ispettore(Base):
    """
    A.5 — Utente autenticato che può firmare ispezioni. Sostituisce il
    vecchio campo libero `ispettore` (stringa scrivibile da chiunque, senza
    alcuna verifica di chi l'ha davvero eseguita).
    Password con hash PBKDF2-HMAC-SHA256 + salt per utente (libreria
    standard `hashlib`, nessuna dipendenza esterna aggiuntiva in un MVP che
    già ne ha poche) — vedi app/auth.py per hashing/verifica.
    """
    __tablename__ = "ispettori"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True)
    password_hash = Column(String, nullable=False)
    password_salt = Column(String, nullable=False)
    attivo = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    inspections = relationship("Inspection", back_populates="ispettore_rel")


class SessionToken(Base):
    """
    Token di sessione bearer, generato al login. Vita lunga (default 30
    giorni, vedi app/auth.py) perché un ispettore sul campo può restare
    offline per giorni: deve poter continuare a registrare ispezioni firmate
    con lo stesso token già ottenuto l'ultima volta che aveva rete, senza
    dover rifare login per forza prima di ogni sincronizzazione.
    """
    __tablename__ = "session_tokens"

    token = Column(String, primary_key=True)
    ispettore_id = Column(Integer, ForeignKey("ispettori.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=False)


class Inspection(Base):
    __tablename__ = "inspections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    structure_id = Column(Integer, ForeignKey("structures.id"), nullable=False)
    data = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # Campo storico, mantenuto solo come cache di visualizzazione (nome
    # dell'ispettore al momento della creazione, per non dover fare un
    # join solo per mostrare una card) — MAI più la fonte autorevole: quella
    # è ispettore_id, valorizzato dal server dall'utente autenticato che ha
    # fatto la richiesta, non da testo che il client può scrivere a piacere.
    ispettore = Column(String, nullable=True)
    ispettore_id = Column(Integer, ForeignKey("ispettori.id"), nullable=True)
    gravita = Column(String, nullable=False)  # "bassa" | "media" | "alta" -- valutazione dell'ispettore umano
    note = Column(Text, nullable=True)
    foto_path = Column(String, nullable=True)
    # A.5 — hash SHA-256 del contenuto del record calcolato dal server al
    # momento della creazione (vedi app/integrity.py). Chiunque riverifichi
    # il record in seguito può ricalcolare lo stesso hash dai campi attuali
    # e confrontarlo: se non coincide più, qualcosa è stato alterato dopo
    # la creazione — rilevante per responsabilità professionale e in caso
    # di indagine successiva a un evento reale.
    hash_verifica = Column(String, nullable=True)
    # A.6 — idempotenza per la coda offline: il client genera un UUID
    # quando accoda l'ispezione (prima ancora di sapere se la richiesta
    # andrà a buon fine), lo rimanda ad ogni tentativo di sync. Se una
    # richiesta risulta già andata a buon fine in precedenza (risposta
    # persa per rete instabile, client che ritenta) il server riconosce
    # il duplicato dal client_uuid invece di creare due ispezioni identiche.
    client_uuid = Column(String, nullable=True)

    structure = relationship("Structure", back_populates="inspections")
    ispettore_rel = relationship("Ispettore", back_populates="inspections")


class MisurazioneEGMS(Base):
    """
    A.7 — Punto della serie storica di deformazione del terreno (InSAR,
    EGMS/Copernicus) per una struttura. Importato da un'esportazione EGMS
    Explorer (CSV/JSON) o, quando disponibile, da un futuro fetch via API
    — vedi app/egms.py per il ragionamento su quale via è davvero
    disponibile oggi. Non è mai calcolato/stimato da questo sistema: il
    valore di spostamento viene sempre da EGMS.
    """
    __tablename__ = "misurazioni_egms"

    id = Column(Integer, primary_key=True, autoincrement=True)
    structure_id = Column(Integer, ForeignKey("structures.id"), nullable=False)
    data = Column(Date, nullable=False)
    spostamento_cumulato_mm = Column(Float, nullable=False)
    fonte = Column(String, nullable=True, default="EGMS Explorer (import manuale)")
    importato_da_id = Column(Integer, ForeignKey("ispettori.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    structure = relationship("Structure", back_populates="misurazioni_egms")


class EventoStruttura(Base):
    """
    A.4 — Storico eventi per struttura. Popolata da SDREA via webhook/push
    quando un evento reale (sisma, alluvione, ...) coinvolge l'area della
    struttura — vedi Parte B.4 della spec e Fase 5 del piano.
    L'intensità stimata arriva da SDREA: non viene mai calcolata qui.
    """
    __tablename__ = "eventi_struttura"

    id = Column(Integer, primary_key=True, autoincrement=True)
    structure_id = Column(Integer, ForeignKey("structures.id"), nullable=False)
    tipo_evento = Column(String, nullable=False)  # "sisma" | "alluvione" | altro
    data = Column(DateTime, nullable=False)
    intensita_stimata = Column(Float, nullable=True)  # valore ricevuto da SDREA, mai calcolato qui
    fonte = Column(String, nullable=True)  # es. "INGV via SDREA"

    structure = relationship("Structure", back_populates="eventi")
