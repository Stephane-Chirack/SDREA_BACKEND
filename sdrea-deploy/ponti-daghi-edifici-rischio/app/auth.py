"""
A.5 — Autenticazione ispettori.

Implementazione volutamente minimale per un MVP (nessuna dipendenza
esterna oltre a quelle già in requirements.txt): hashing password con
PBKDF2-HMAC-SHA256 (libreria standard `hashlib`) e token di sessione
bearer generati con `secrets.token_urlsafe` — non JWT, per restare senza
dipendenze e senza dover gestire chiavi di firma: un token è solo un
identificatore casuale che il server associa a un ispettore in tabella,
esattamente come una sessione server-side classica.

Vita del token lunga (30 giorni di default) perché un ispettore sul campo
dopo un evento reale può restare offline per giorni (vedi A.6): deve poter
continuare a firmare ispezioni con lo stesso token, sincronizzate quando
la rete torna, senza dover rifare login nel frattempo.
"""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Header, HTTPException

TOKEN_TTL_DAYS = 30
PBKDF2_ITERATIONS = 260_000


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, password_hash)


def new_token() -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=TOKEN_TTL_DAYS)
    return token, expires_at


def make_service_auth_dependency():
    """
    Autenticazione servizio-a-servizio per il canale SDREA <-> Ponti-Daghi-
    Edifici (endpoint /api/sdrea/eventi e /api/sdrea/readiness-feed): non è
    un'azione umana, quindi niente login/bearer token da ispettore, ma una
    chiave condivisa semplice in header, impostata via variabile
    d'ambiente su entrambi i servizi — stesso principio già usato nel
    progetto per i parametri "DA VALIDARE" (nessuna dipendenza nuova,
    nessuna chiave da gestire oltre a una variabile d'ambiente).

    Comportamento esplicito e dichiarato: se SDREA_SERVICE_KEY non è
    impostata (es. sviluppo locale), il canale resta aperto come oggi,
    per non rompere l'uso locale senza configurazione — appena la
    variabile viene impostata su entrambi i backend in produzione,
    l'autenticazione si attiva automaticamente, senza altre modifiche al
    codice. Non è mTLS né un meccanismo sofisticato: è la protezione
    minima ragionevole per un canale server-to-server tra due servizi
    privati, non ancora una soluzione "enterprise".
    """
    expected_key = os.environ.get("SDREA_SERVICE_KEY")

    def verify_service_key(x_sdrea_service_key: str | None = Header(default=None)):
        if not expected_key:
            return  # SDREA_SERVICE_KEY non configurata: canale aperto (vedi nota sopra)
        if not x_sdrea_service_key or not hmac.compare_digest(x_sdrea_service_key, expected_key):
            raise HTTPException(status_code=401, detail="Chiave di servizio mancante o non valida.")

    return verify_service_key


def make_auth_dependency(SessionLocal, Ispettore, SessionToken):
    """
    Factory che chiude su SessionLocal/i modelli per evitare import
    circolari con app/main.py (che li definisce/istanzia).
    Ritorna una dependency FastAPI da usare come:
        ispettore: Ispettore = Depends(get_current_ispettore)
    """

    def get_current_ispettore(authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Autenticazione richiesta: effettua il login.")
        token = authorization[len("Bearer "):].strip()

        db = SessionLocal()
        try:
            session = db.query(SessionToken).filter(SessionToken.token == token).first()
            if not session:
                raise HTTPException(status_code=401, detail="Token non valido o scaduto: rifai il login.")
            if session.expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
                db.delete(session)
                db.commit()
                raise HTTPException(status_code=401, detail="Sessione scaduta: rifai il login.")
            ispettore = db.query(Ispettore).filter(Ispettore.id == session.ispettore_id).first()
            if not ispettore or not ispettore.attivo:
                raise HTTPException(status_code=401, detail="Utente non trovato o disattivato.")
            # Distacchiamo l'oggetto dalla sessione prima di chiuderla, per
            # poterlo ancora leggere (non modificare/salvare) dopo il return
            # senza incorrere in DetachedInstanceError sui suoi attributi.
            db.expunge(ispettore)
            return ispettore
        finally:
            db.close()

    return get_current_ispettore
