"""
Modelli dati del motore idraulico SDREA (rischio alluvionale/idrogeologico) —
stessa architettura non negoziabile del motore sismico (sdrea-motore-sismico/):
database SQLite proprio, separato sia da Ponti-Daghi-Edifici sia dal motore
sismico. Nessun import di codice tra backend: solo HTTP.

Perché un terzo backend e non un endpoint dentro il motore sismico: la spec
originale (Parte B) tratta esplicitamente "sisma" e "alluvione" come due
`tipo_evento` diversi in EventoStruttura (vedi ponti-daghi-edifici-rischio/
app/models.py), ma il modello scientifico che sta dietro un evento
idraulico non ha nulla in comune con un modello di attenuazione sismica
(non decade radialmente da un epicentro: dipende da livelli idrometrici,
soglie di criticità e propagazione lungo un corso d'acqua). Tenerli in due
motori separati, con lo stesso contratto verso Ponti-Daghi-Edifici e verso
Vicky, è la stessa scelta architettonica "ad adattatori" già presente nel
resto del sistema (un sensore/rischio nuovo è un nuovo modulo, non una
riscrittura) — vedi README.md.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean, ForeignKey, JSON,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

PRIORITA_VALORI = ("bassa", "media", "alta", "critica")
RUOLI_VALORI = ("operatore_civile", "operatore_difesa", "amministratore")
LIVELLI_ALLERTA = ("verde", "giallo", "arancione", "rosso")  # colori standard Protezione Civile


class EventoIdraulico(Base):
    """
    Lettura idrometrica reale (o soglia di allerta superata) su una
    stazione di monitoraggio, ingerita dal feed ARPA/Protezione Civile
    Regionale — vedi app/arpa_client.py per la nota importante sull'accesso
    reale a questi dati.

    Non è "un evento" nel senso puntuale di un sisma: una piena è un
    processo che dura ore/giorni sulla stessa stazione, quindi qui si
    salva ogni lettura che supera la soglia1 (ordinaria vigilanza) come un
    nuovo record — la storia delle letture su una stazione è essa stessa
    l'informazione utile (livello in salita o in discesa), non solo
    l'ultimo valore.
    """
    __tablename__ = "eventi_idraulici"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Idempotenza: come ingv_event_id nel motore sismico, identifica una
    # singola lettura in modo univoco (stazione + timestamp) così un
    # ripoll dello stesso intervallo non duplica la riga.
    lettura_id = Column(String, nullable=False, unique=True)
    codice_stazione = Column(String, nullable=False)
    nome_stazione = Column(String, nullable=True)
    corso_d_acqua = Column(String, nullable=True)
    lat_stazione = Column(Float, nullable=False)
    lon_stazione = Column(Float, nullable=False)

    livello_idrometrico_m = Column(Float, nullable=False)
    # Soglie idrometriche ufficiali della stazione (pubblicate da ARPA/
    # Autorità di Bacino insieme al dato di livello) — MAI calcolate qui,
    # sempre quelle dichiarate dalla fonte per quella specifica stazione,
    # perché ogni stazione ha soglie diverse legate alla sua sezione
    # idraulica locale.
    soglia1_ordinaria_m = Column(Float, nullable=True)
    soglia2_moderata_m = Column(Float, nullable=True)
    soglia3_elevata_m = Column(Float, nullable=True)
    livello_allerta = Column(String, nullable=True)  # "verde"|"giallo"|"arancione"|"rosso", derivato dalle soglie sopra

    timestamp = Column(DateTime, nullable=False)
    fonte = Column(String, nullable=False, default="ARPA")
    processato_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    valutazioni = relationship("ValutazionePrioritaIdraulica", back_populates="evento", order_by="desc(ValutazionePrioritaIdraulica.priorita_rank)")


class ValutazionePrioritaIdraulica(Base):
    """
    Priorità calcolata per una struttura in risposta a una lettura
    idrometrica critica — stesso ruolo di ValutazionePriorita nel motore
    sismico: una riga per coppia (evento, struttura), con uno snapshot dei
    fattori usati nel calcolo per la spiegabilità di Vicky, così una
    domanda "perché" non richiede una nuova chiamata HTTP a
    Ponti-Daghi-Edifici.
    """
    __tablename__ = "valutazioni_priorita_idrauliche"

    id = Column(Integer, primary_key=True, autoincrement=True)
    evento_id = Column(Integer, ForeignKey("eventi_idraulici.id"), nullable=False)
    structure_id = Column(Integer, nullable=False)  # id esterno, lato Ponti-Daghi-Edifici
    structure_nome = Column(String, nullable=True)
    structure_lat = Column(Float, nullable=True)
    structure_lon = Column(Float, nullable=True)

    distanza_km = Column(Float, nullable=False)  # distanza dalla stazione idrometrica (proxy — vedi app/idrometria.py)
    livello_relativo_m = Column(Float, nullable=False)  # quanto il livello supera la soglia di riferimento usata
    priorita_finale = Column(String, nullable=False)
    priorita_rank = Column(Integer, nullable=False)

    fattori = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    evento = relationship("EventoIdraulico", back_populates="valutazioni")


class StatoAllertaZona(Base):
    """
    Stato di allerta ufficiale per zona e fenomeno, dalla API pubblica
    reale di Allerta Meteo Emilia-Romagna (Agenzia regionale protezione
    civile) — vedi app/allerta_meteo_client.py. A differenza di
    EventoIdraulico (una lettura numerica di una singola stazione), questo
    è il segnale categorico ufficiale a livello di zona di allertamento
    (colori standard Protezione Civile), collegato al bollettino di
    vigilanza regionale vigente.

    Idempotenza: una riga è identificata da (data_emissione_raw, zona,
    fenomeno) — un nuovo poll dello stesso bollettino non duplica le righe.
    """
    __tablename__ = "stati_allerta_zona"

    id = Column(Integer, primary_key=True, autoincrement=True)
    zona = Column(String, nullable=False)
    fenomeno = Column(String, nullable=False)  # "idraulica", "idrogeologica", "temporali", "vento", ...
    colore = Column(String, nullable=False)  # verde|giallo|arancione|rosso|sconosciuto
    colore_raw = Column(String, nullable=True)  # valore originale inglese (green/yellow/orange/red)
    titolo_bollettino = Column(String, nullable=True)
    link_bollettino = Column(String, nullable=True)
    data_emissione_raw = Column(String, nullable=True)
    data_inizio_raw = Column(String, nullable=True)
    data_fine_raw = Column(String, nullable=True)
    fetch_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    fonte = Column(String, nullable=False, default="Allerta Meteo Emilia-Romagna")


class LetturaBollettinoMonitoraggio(Base):
    """
    Lettura numerica per sottobacino/stazione dal bollettino di
    monitoraggio idrologico ufficiale (vedi app/allerta_meteo_client.py).
    Tenuta separata da EventoIdraulico perché questa fonte non fornisce
    lat/lon né soglie1/2/3 ufficiali per sottobacino — vedi il limite
    dichiarato nel docstring del modulo client. Serve come secondo segnale
    ufficiale reale che Vicky può incrociare con EventoIdraulico e con
    StatoAllertaZona, non come sostituto del feed per-stazione con soglie.

    Idempotenza: una riga è identificata da (bollettino_id,
    nome_sottobacino, ora_osservazione_raw).
    """
    __tablename__ = "letture_bollettino_monitoraggio"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bollettino_id = Column(String, nullable=False)
    nome_bacino = Column(String, nullable=True)
    nome_sottobacino = Column(String, nullable=False)
    livello_osservato = Column(Float, nullable=True)
    ora_osservazione_raw = Column(String, nullable=True)
    livello_colmo_previsto = Column(Float, nullable=True)
    ora_colmo_previsto_raw = Column(String, nullable=True)
    inizio_monitoraggio_raw = Column(String, nullable=True)
    fine_monitoraggio_raw = Column(String, nullable=True)
    nota_bollettino = Column(String, nullable=True)
    fetch_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    fonte = Column(String, nullable=False, default="Allerta Meteo Emilia-Romagna")


class Operatore(Base):
    """B.5-equivalente — stesso contratto RBAC del motore sismico, vedi app/rbac.py."""
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
    Registro squadre/mezzi — stesso modulo concettuale del motore sismico
    (vedi la nota onesta su M4 in sdrea-motore-sismico/app/models.py: vale
    identica qui). Tabella separata e non condivisa con il motore sismico
    per rispettare l'isolamento dei database, anche se in produzione un
    vero servizio M4 condiviso servirebbe entrambi i motori senza
    duplicazione — nota esplicita anche qui.
    """
    __tablename__ = "squadre_logistica"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String, nullable=False)
    tipo = Column(String, nullable=False)  # es. "squadra VVFF", "mezzo anfibio", "idrovora", "gommone", ...
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    disponibile = Column(Boolean, nullable=False, default=True)
    note = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AssegnazioneRisorsa(Base):
    """Identico vincolo del motore sismico: mai creata senza conferma umana esplicita."""
    __tablename__ = "assegnazioni_risorsa"

    id = Column(Integer, primary_key=True, autoincrement=True)
    squadra_id = Column(Integer, ForeignKey("squadre_logistica.id"), nullable=False)
    structure_id = Column(Integer, nullable=False)
    evento_id = Column(Integer, ForeignKey("eventi_idraulici.id"), nullable=True)
    confermata_da_id = Column(Integer, ForeignKey("operatori.id"), nullable=False)
    note = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
