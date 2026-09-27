# SDREA — Motore Emergenza Sismica Reale

Backend **separato** da "Ponti, Daghi e Edifici a Rischio" (Parte B della spec
"SDREA + Ponti, Daghi e Edifici a Rischio" v1.0). Principio architetturale
non negoziabile della spec: i due sistemi restano backend separati con
database propri, collegati solo via API — questo servizio non importa né
condivide codice con `ponti-daghi/`, gli parla solo via HTTP.

## Cosa fa (Fase 5: B.2 + B.3, più B.5 — RBAC)

1. **B.2 — Ingestione eventi sismici reali**: consuma il feed pubblico INGV
   (FDSN event webservice), per ogni evento nuovo calcola la distanza da
   ogni struttura esposta dal readiness-feed di Ponti-Daghi-Edifici e stima
   l'intensità di scuotimento con un modello di attenuazione — vedi
   `app/attenuation.py` per la nota importante su questi parametri (DA
   VALIDARE, non decisi qui). Per ogni struttura coinvolta invia un record
   `EventoStruttura` a Ponti-Daghi-Edifici via `POST /api/sdrea/eventi`.
2. **B.3 — Motore di priorità combinata**: calcola `ValutazionePriorita`
   per ogni struttura coinvolta da un evento — vedi `app/priority.py` per
   la stessa nota sui pesi/soglie (DA VALIDARE).
3. **B.5 — RBAC**: vedi `app/rbac.py` per la nota su cosa significa "riuso
   del modulo M8" in questo contesto (non ho accesso in questa sessione a
   un servizio M8 condiviso già distribuito: ho costruito un modulo RBAC
   reale, autonomo, ma con lo stesso contratto di ruoli/permessi descritto
   per M8 nella spec Defense, così che sia sostituibile con una chiamata al
   vero M8 in futuro senza toccare gli endpoint che lo usano).

B.4 (nuove capacità di Vicky) è la Fase 6, non ancora implementata a questo
punto — vedi il resto della consegna.

## Avvio locale

```
pip install -r requirements.txt
export PONTI_DAGHI_BASE_URL=http://localhost:8000   # dove gira il backend Ponti-Daghi-Edifici
uvicorn app.main:app --reload --port 8001
```

## Nota importante su INGV e sull'ambiente di sviluppo

Il client INGV (`app/ingv_client.py`) chiama il vero endpoint pubblico
`https://webservices.ingv.it/fdsnws/event/1/query` (formato FDSN standard,
`format=text`, colonne pipe-separated — documentato da INGV). Non ho potuto
verificarlo end-to-end da questa sandbox di sviluppo: l'egress di rete qui è
filtrato e la connessione a `webservices.ingv.it` è stata rifiutata dal
proxy (non un problema del codice, un limite dell'ambiente di sviluppo).
Il parsing e tutta la logica a valle (distanza, attenuazione, priorità,
invio a Ponti-Daghi) sono invece stati testati con dati reali, sia via una
fixture del formato di testo INGV pubblicato (per il parser) sia end-to-end
con un server locale finto che simula l'endpoint INGV — vedi
`app/tests/`. **Prima di andare in produzione, l'ingegnere deve verificare
una chiamata reale al servizio INGV da un ambiente con accesso a
internet** (è probabile funzioni così com'è, dato che è l'endpoint
pubblico standard, ma non è stato osservato un byte reale di risposta INGV
in questa sessione, e questo va detto esplicitamente).
