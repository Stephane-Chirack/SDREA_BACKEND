# SDREA — Motore Emergenza Idraulica Reale

Terzo backend **separato**, stessa architettura non negoziabile degli altri
due: database SQLite proprio, nessun import di codice da/verso
`ponti-daghi-edifici-rischio/` o `sdrea-motore-sismico/` — parla con loro
solo via HTTP.

## Perché un motore separato invece di estendere quello sismico

Un sisma si attenua in modo approssimativamente radiale da un epicentro.
Una piena si propaga lungo la rete idrografica: non è la stessa fisica, e
forzarla nello stesso modello di attenuazione avrebbe prodotto un risultato
scorretto travestito da "riuso del codice". La scelta architettonica —
motori intercambiabili con lo stesso contratto verso Ponti-Daghi-Edifici e
verso Vicky — è la stessa logica "ad adattatori" già presente nel resto del
sistema (SDREA.html: un sensore nuovo è un nuovo `SensorAdapter`, non una
riscrittura). Un terzo tipo di rischio (es. incendio, frana) seguirebbe lo
stesso pattern: un quarto backend, stesso contratto.

## Cosa fa

1. **Ingestione letture idrometriche**: legge letture di livello da stazioni
   di monitoraggio (via `POST /api/eventi/manuale` per test/demo, o via
   `POST /api/eventi/poll-arpa` quando `ARPA_IDRO_BASE_URL` è configurato —
   vedi **la nota importante sotto**), deriva il colore di allerta standard
   Protezione Civile (verde/giallo/arancione/rosso) dalle soglie ufficiali
   della stazione.
2. **Filtro di rilevanza per corso d'acqua** (`app/idrometria.py`): una
   struttura è valutata solo se il suo `corso_acqua_riferimento` (dato
   ufficiale lato Ponti-Daghi-Edifici) corrisponde al corso d'acqua della
   stazione, o se il dato manca (in quel caso valutata comunque, con
   `incertezza_corso_acqua: true` nei fattori — mai un'esclusione o
   un'inclusione silenziosa).
3. **Motore di priorità combinata** (`app/priority.py`): stessa forma dello
   score additivo del motore sismico, fattori diversi (superamento soglia
   idrometrica, zona PGRA, presenza di argine, rischio diretto per quota,
   più gli stessi fattori di esposizione già in comune con il motore
   sismico — edifici sensibili, percorsi alternativi).
4. **Invio a Ponti-Daghi-Edifici**: `POST /api/sdrea/eventi` con
   `tipo_evento="alluvione"` — stesso endpoint già usato dal motore sismico
   (`tipo_evento="sisma"`), il campo era già generico nella spec originale.
5. **Vicky**: stesse tre query del motore sismico (priorità, squadre
   vicine, spiegazione) più una query specifica del dominio idraulico
   ("qual è il livello di allerta attuale").
6. **RBAC e Logistica**: stessi moduli, stessa nota onesta su M8/M4 del
   motore sismico — duplicati qui (non importati) per rispettare
   l'isolamento tra backend.

## Estensione fatta a Ponti-Daghi-Edifici per questo motore

`Structure` ha 5 nuove colonne (vedi `app/models.py` lì, sezione "A.2bis —
Vulnerabilità idraulica", e la migrazione additiva in `app/migrations.py`):
`zona_pgra` (classificazione ufficiale PGRA: P1/P2/P3), `quota_slm_m`,
`distanza_corso_acqua_km`, `corso_acqua_riferimento`,
`presenza_argine_protezione`. Stesso principio di `ridondanza`/
`normativa_antisismica`: dati ufficiali importati o inseriti da un tecnico,
mai calcolati da questo sistema. Il readiness-feed (`/api/sdrea/readiness-feed`)
ora li espone; nessun altro comportamento esistente è cambiato (colonne
tutte nullable, migrazione additiva, database esistenti restano validi).

## *** Aggiornamento — due fonti ufficiali reali ora collegate (app/allerta_meteo_client.py) ***

Dopo una ricerca dedicata per identificare la fonte idrometrica più
affidabile disponibile (vedi sotto per cosa è stato scartato e perché),
questo motore si collega oggi a **due endpoint pubblici reali** della API
ufficiale di Allerta Meteo Emilia-Romagna (Agenzia regionale per la
sicurezza territoriale e la protezione civile), **interrogati dal vivo e
verificati con dati correnti reali durante questa sessione di sviluppo**
(22/09/2026), non solo documentati sulla carta:

- `POST /api/eventi/poll-stato-allerta` → `GET /o/get-stato-allerta`:
  stato di allerta ufficiale corrente per zona di allertamento e fenomeno
  (idraulica, idrogeologica, temporali, vento, ...), colori standard
  Protezione Civile, collegato al bollettino di vigilanza vigente.
  Risposta reale catturata: bollettino 144/2026, tutte le zone in verde
  alla data del test.
- `POST /api/eventi/poll-bollettino-monitoraggio` → `GET
  /o/get-bollettino-monitoraggio`: letture numeriche reali per
  sottobacino/stazione (livello osservato, ora, colmo di piena previsto
  quando disponibile) su ~29 bacini idrografici regionali. Risposta reale
  catturata: bollettino 010/2025/04ULTIMO, bacino SENIO con 6 sottobacini
  e letture numeriche autentiche (es. Fusignano 4.06 m, Cotignola 8.02 m).

Entrambi gli endpoint sono pubblici, senza autenticazione, documentati
nella pagina ufficiale `/sviluppatori` del portale. Il parsing di entrambi
è testato contro le risposte reali catturate (non fixture inventate) in
`app/tests/test_allerta_meteo_parsing.py`.

**Limite dichiarato, ancora aperto**: il bollettino di monitoraggio dà un
nome di sottobacino (es. "Fusignano") e un valore, ma non lat/lon né
soglie1/2/3 ufficiali per quel punto — a differenza del formato pensato per
`app/arpa_client.py`. Per questo le sue letture sono salvate in un modello
dedicato (`LetturaBollettinoMonitoraggio`), non forzate nel formato
per-stazione con soglie: è un secondo segnale ufficiale reale che **Vicky
incrocia** con lo stato di allerta per zona e con le letture di stazione
già presenti (nuova query: "qual è lo stato ufficiale regionale", vedi
`app/vicky.py::rispondi_stato_ufficiale`), non un sostituto del feed
per-stazione con soglie assolute — quello resta da costruire con
l'anagrafica ufficiale delle stazioni (idealmente fornita da ARPAE/l'Agenzia
regionale), motivo per cui `app/arpa_client.py` resta anche come adattatore
configurabile per quando quella fonte sarà scelta.

**Limite dichiarato, non di scelta ma di rete di questo ambiente di
sviluppo**: la sandbox in cui questo codice è stato scritto ha un proxy di
uscita che rifiuta le connessioni verso questi domini (stessa restrizione
già nota per INGV — vedi sotto), quindi il client è stato verificato con
richieste dirette dall'esterno della sandbox durante la ricerca (risposte
reali catturate e incluse come fixture), ma **non è mai stato eseguito
end-to-end come server in esecuzione in questo ambiente**: `httpx.get()`
verso questi endpoint, lanciato da qui, fallisce con lo stesso errore di
proxy di un `curl` diretto. Il codice è corretto e testato contro il
formato reale; il primo avvio in un ambiente con uscita di rete aperta
(l'infrastruttura di produzione, o il computer dello sviluppatore) è il
passo che resta per la verifica end-to-end completa, esattamente come per
INGV nel motore sismico.

Sono stati valutati e **scartati** come fonte primaria, con motivazione:
`Dext3r`/`simc.arpae.it/rt_data` (solo interfaccia web con export via email,
nessuna API REST/JSON documentata), `dati.emilia-romagna.it`/`dati.arpae.it`
(portale CKAN reale ma nessun dataset idrometrico in tempo reale con
datastore attivo confermato), Autorità di Bacino del Po (dashboard
Power BI/Infogram, aggiornamento settimanale, non in tempo reale).

## *** Nota importante — la stessa onestà già usata per INGV (fonte per-stazione con soglie) ***

Il feed sismico INGV è un unico webservice pubblico stabile: il motore
sismico lo chiama direttamente. Una fonte idrometrica per-stazione con
soglie ufficiali (formato di `app/arpa_client.py`, con lat/lon e
soglia1/2/3 per ogni stazione) **non ha un equivalente altrettanto
semplice**: nessuna delle fonti valutate la espone con una API REST/JSON
pubblica e documentata (vedi sopra). Per questo `app/arpa_client.py` resta
un adattatore configurabile in attesa che quella fonte venga scelta —
idealmente in collaborazione con ARPAE/l'Agenzia regionale, che conosce
l'anagrafica esatta delle stazioni.

Per questo `app/arpa_client.py` è scritto per essere agnostico rispetto alla
fonte: espone `parse_arpa_json()`/`parse_arpa_csv()` (entrambi testati, vedi
`app/tests/test_arpa_parsing.py`) e un endpoint HTTP configurabile
(`ARPA_IDRO_BASE_URL`). **Questo è il singolo passo concreto che separa oggi
questo motore dall'avere anche un feed per-stazione con soglie assolute
quanto quello sismico**: scegliere il canale ARPA/Regione ER, e scrivere un
piccolo adattatore che ne mappi la risposta nel formato JSON già
documentato nel modulo (o pubblicarla già in
quel formato, se si controlla la fonte). Tutta la logica a valle — parsing,
filtro geografico, priorità, invio a Ponti-Daghi-Edifici, Vicky — è già
completa, testata e non cambierà quando la fonte reale verrà collegata.

Nel frattempo, `POST /api/eventi/manuale` copre demo e collaudo end-to-end
esattamente come per il motore sismico (vedi il percorso di prova sotto) —
verificato in questa sessione con i due backend realmente in esecuzione,
non solo sulla carta.

## Avvio locale

```
cd sdrea-motore-idraulico
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export PONTI_DAGHI_BASE_URL=http://localhost:8000
uvicorn app.main:app --reload --port 8002
```

## Percorso di prova end-to-end (via curl) — verificato in questa sessione

```bash
# 1. Struttura con vulnerabilità idraulica su Ponti-Daghi-Edifici
curl -X POST http://localhost:8000/api/structures -H "Content-Type: application/json" \
  -d '{"name":"Argine Pontelagoscuro","tipo":"diga","lat":44.89,"lon":11.61,
       "zona_pgra":"P3","quota_slm_m":3.5,"distanza_corso_acqua_km":0.2,
       "corso_acqua_riferimento":"Po","presenza_argine_protezione":false}'

# 2. Operatore sul motore idraulico
curl -X POST http://localhost:8002/api/auth/register -H "Content-Type: application/json" \
  -d '{"nome":"Laura Bianchi","email":"laura@example.com","password":"password123"}'
# (usa il token restituito come "Authorization: Bearer <token>" sotto)

# 3. Lettura idrometrica critica sul Po, vicino alla struttura
curl -X POST http://localhost:8002/api/eventi/manuale -H "Content-Type: application/json" -H "Authorization: Bearer <token>" \
  -d '{"codice_stazione":"16000200","nome_stazione":"Ferrara Pontelagoscuro","corso_d_acqua":"Po",
       "lat_stazione":44.8886,"lon_stazione":11.6103,"livello_idrometrico_m":5.3,
       "soglia1_ordinaria_m":3.0,"soglia2_moderata_m":4.0,"soglia3_elevata_m":5.0}'

# 4. Chiedi a Vicky
curl -X POST http://localhost:8002/api/vicky/query -H "Content-Type: application/json" -H "Authorization: Bearer <token>" \
  -d '{"domanda":"quali infrastrutture richiedono ispezione prioritaria"}'
curl -X POST http://localhost:8002/api/vicky/query -H "Content-Type: application/json" -H "Authorization: Bearer <token>" \
  -d '{"domanda":"qual è il livello di allerta attuale"}'
```

Su Ponti-Daghi-Edifici, `GET /api/structures/1` mostra ora anche
l'`EventoStruttura` (`tipo_evento: "alluvione"`) ricevuto da questo motore.

## Parametri "DA VALIDARE" — non usare in produzione senza conferma di un idraulico

- `app/idrometria.py` — `SDREA_IDRO_MARGINE_QUOTA_M`,
  `SDREA_IDRO_DISTANZA_MASSIMA_KM`: il proxy di distanza in linea d'aria per
  "quanto lontano lungo il fiume" è dichiarato esplicitamente impreciso nel
  docstring del modulo — va sostituito da un vero modello di routing
  idraulico prima di un uso operativo che decida priorità in base al tempo
  di arrivo dell'onda di piena.
- `app/priority.py` — tutti i pesi (`SDREA_IDRO_PESO_*`) e le soglie
  (`SDREA_IDRO_SOGLIA_*`): segnaposto, stessa natura di quelli del motore
  sismico.
- `app/main.py` — `SDREA_IDRO_LIVELLO_NOTIFICA`: soglia di colore per la
  notifica proattiva di Vicky.

## Vincolo di sicurezza su Vicky — applicato per costruzione

Identico al motore sismico: `app/vicky.py` è sola lettura (nessuna funzione
scrive nel database), e l'unico endpoint che assegna una risorsa
(`POST /api/assegnazioni/conferma`) richiede sempre un operatore
autenticato — nessun percorso di codice crea un'assegnazione senza quella
conferma esplicita.
