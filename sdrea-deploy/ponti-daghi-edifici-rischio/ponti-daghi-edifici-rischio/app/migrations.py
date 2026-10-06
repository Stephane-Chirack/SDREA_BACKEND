"""
Migrazione dello schema SQLite, additiva e non distruttiva.

SQLAlchemy `Base.metadata.create_all(engine)` crea le tabelle mancanti ma
NON aggiunge colonne nuove a tabelle già esistenti — quindi un database
`ponti_daghi.db` creato con lo schema precedente (prima di A.1/A.2/A.3)
resterebbe privo delle nuove colonne, e l'app fallirebbe alla prima query
che le referenzia.

Questa funzione ispeziona lo schema reale via `PRAGMA table_info` e
aggiunge solo le colonne mancanti con `ALTER TABLE ... ADD COLUMN`,
lasciando intatte tabelle e righe esistenti. Va chiamata dopo
`Base.metadata.create_all(engine)` (che crea le tabelle nuove di zecca,
es. `eventi_struttura`) e prima di servire qualunque richiesta.

Adatta per SQLite/MVP di queste dimensioni; se il progetto cresce e serve
gestire anche rimozioni/rinomini di colonne o rollback, il passo naturale
successivo è introdurre Alembic — non fatto qui per non aggiungere una
dipendenza a un MVP che ne ha ancora bisogno solo in un verso (aggiungere
colonne).
"""

from sqlalchemy import inspect, text


# Colonne attese per tabella, con il tipo SQL da usare in ADD COLUMN se
# mancante. Tenuto in sync manualmente con app/models.py — un test di
# avvio (vedi test_migrations.py) verifica che coincidano.
EXPECTED_COLUMNS = {
    "structures": {
        "numero_piani": "INTEGER",
        "destinazione_uso": "VARCHAR",
        "tipologia_strutturale": "VARCHAR",
        "ridondanza": "VARCHAR",
        "zona_sismica": "INTEGER",
        "normativa_antisismica": "BOOLEAN",
        "ultima_verifica_tecnica": "DATE",
        "traffico_giornaliero_stimato": "INTEGER",
        "percorsi_alternativi": "BOOLEAN",
        "edifici_sensibili_vicini": "INTEGER",
        "popolazione_servita_stimata": "INTEGER",
        "updated_at": "DATETIME",  # A.6 — rilevamento conflitti su sync offline
        "client_uuid": "VARCHAR",  # A.6 — idempotenza sync offline
    },
    "inspections": {
        # Fase 3 (A.5) — aggiunte qui in anticipo nella tabella così la
        # migrazione resta un unico punto di verità; le colonne restano
        # nullable finché main.py non richiede ispettore_id/hash_verifica.
        "ispettore_id": "INTEGER",
        "hash_verifica": "VARCHAR",
        "client_uuid": "VARCHAR",  # A.6 — idempotenza sync offline
    },
}


def run_migrations(engine):
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table_name, columns in EXPECTED_COLUMNS.items():
            if table_name not in existing_tables:
                # La tabella non esiste ancora: la creerà create_all() a
                # parte, con già tutte le colonne — niente da fare qui.
                continue
            existing_columns = {c["name"] for c in inspector.get_columns(table_name)}
            for col_name, col_type in columns.items():
                if col_name in existing_columns:
                    continue
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"))
                print(f"[migrations] Aggiunta colonna mancante: {table_name}.{col_name} ({col_type})")
