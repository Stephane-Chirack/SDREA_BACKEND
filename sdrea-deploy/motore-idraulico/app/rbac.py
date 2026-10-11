"""
RBAC del motore idraulico — stesso contratto e stessa nota onesta su M8 del
motore sismico (vedi sdrea-motore-sismico/app/rbac.py): un vero modulo
RBAC funzionante, isolato in questo file, sostituibile da una chiamata a un
futuro servizio M8 condiviso senza toccare gli endpoint che lo usano.
Duplicato deliberatamente (non importato dal motore sismico) per
rispettare l'isolamento tra backend.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Header, HTTPException

TOKEN_TTL_DAYS = 14
PBKDF2_ITERATIONS = 260_000

PERMESSI_PER_RUOLO = {
    "operatore_civile": {"vedi_priorita", "vedi_eventi"},
    "operatore_difesa": {"vedi_priorita", "vedi_eventi"},
    "amministratore": {"vedi_priorita", "vedi_eventi", "gestisci_operatori", "configura_soglie"},
}


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


def make_auth_dependency(SessionLocal, Operatore, SessionToken):
    def get_current_operatore(authorization: str | None = Header(default=None)):
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
            operatore = db.query(Operatore).filter(Operatore.id == session.operatore_id).first()
            if not operatore or not operatore.attivo:
                raise HTTPException(status_code=401, detail="Utente non trovato o disattivato.")
            db.expunge(operatore)
            return operatore
        finally:
            db.close()

    return get_current_operatore


def richiede_permesso(operatore, permesso: str) -> None:
    permessi = PERMESSI_PER_RUOLO.get(operatore.ruolo, set())
    if permesso not in permessi:
        raise HTTPException(status_code=403, detail=f"Il ruolo '{operatore.ruolo}' non ha il permesso '{permesso}'.")
