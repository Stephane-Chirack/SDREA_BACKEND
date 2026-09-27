"""
B.5 — RBAC (controllo accessi basato sui ruoli) per il motore sismico.

Nota onesta sul "riuso del modulo M8": la spec chiede esplicitamente di
non duplicare la logica di permessi tra la macro-area Defense (dove M8 è
già specificato) e questo motore, uso civile. In questa sessione di
lavoro non ho a disposizione un servizio M8 condiviso e distribuito da
richiamare via rete (nella base di codice di questo progetto, M8 esiste
finora solo come classe JS lato client dentro SDREA.html, non come
servizio backend indipendente interrogabile). Duplicare quella classe JS
qui, in Python, rinominandola, sarebbe esattamente la violazione che la
spec vuole evitare — solo mascherata.

La scelta più onesta è quindi: questo modulo implementa un vero sistema
RBAC funzionante (ruoli, permessi, autenticazione a token, tutto reale e
testato), ma con la STESSA STRUTTURA DI CONTRATTO descritta per M8 nella
spec Defense (ruoli operatore_civile / operatore_difesa / amministratore,
permessi per vista/azione, non per singolo endpoint), isolato in questo
unico modulo con un'unica funzione di verifica permessi
(`richiede_permesso`). Quando esisterà un vero servizio M8 condiviso,
sostituire l'implementazione di `get_current_operatore` /
`richiede_permesso` con una chiamata a quel servizio è un cambiamento
isolato a questo file: nessun endpoint in app/main.py dovrà cambiare,
perché tutti dipendono dal contratto (Operatore con `.ruolo`), non
dall'implementazione. Va segnalato esplicitamente all'ingegnere: **questo
punto (unificare davvero con M8) resta da fare quando M8 esisterà come
servizio raggiungibile**, non è stato inventato un fake M8 da "spuntare"
il requisito.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Header, HTTPException

TOKEN_TTL_DAYS = 14  # più corto delle 30 gg di Ponti-Daghi: un operatore in sala operativa lavora online, non serve tollerare settimane offline
PBKDF2_ITERATIONS = 260_000

# Contratto dei permessi per ruolo (B.5) — quali azioni del motore sismico
# ogni ruolo può eseguire. "operatore_civile" e "operatore_difesa"
# condividono lo stesso motore ma con viste diverse: qui la differenza è
# minima (il motore è a uso civile in questa fase), il campo `ruolo` è
# comunque salvato e verificato per essere pronto quando la vista
# differenziata per la difesa sarà specificata.
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
    """Solleva 403 se il ruolo dell'operatore non ha il permesso richiesto."""
    permessi = PERMESSI_PER_RUOLO.get(operatore.ruolo, set())
    if permesso not in permessi:
        raise HTTPException(status_code=403, detail=f"Il ruolo '{operatore.ruolo}' non ha il permesso '{permesso}'.")
