"""
A.5 — Tracciabilità/integrità del record di ispezione.

`hash_verifica` è lo SHA-256 del contenuto "significativo" del record al
momento della creazione: chiunque abbia accesso al record in un secondo
momento può ricalcolarlo dai valori correnti e confrontarlo con quello
salvato. Se non coincidono più, qualcuno ha alterato il record dopo la
creazione senza passare da un percorso che aggiorna anche l'hash (cioè:
un update diretto sul database, non un'azione applicativa prevista — qui
non esiste alcun endpoint che modifichi un'ispezione già creata, di
proposito: un'ispezione è un evento storico, non un dato editabile).

Non è una firma crittografica legata all'identità dell'ispettore (per
quella serve WebAuthn o una PKI reale, fuori scope per un MVP) — è un
controllo di integrità: rileva la manomissione, non prova chi ha scritto
cosa oltre a quanto già garantito da ispettore_id (utente autenticato al
momento della creazione).
"""

import hashlib


def compute_inspection_hash(
    structure_id: int,
    data_iso: str,
    ispettore_id: int | None,
    gravita: str,
    note: str,
    foto_path: str | None,
) -> str:
    payload = "|".join([
        str(structure_id),
        data_iso,
        str(ispettore_id) if ispettore_id is not None else "",
        gravita,
        note or "",
        foto_path or "",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
