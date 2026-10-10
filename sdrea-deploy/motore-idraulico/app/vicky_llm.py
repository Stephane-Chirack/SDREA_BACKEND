"""
Livello linguistico opzionale per Vicky — 2026-10-10.

Vincolo invariato (vedi vicky.py, mai cambiato qui): Vicky resta un modulo
di SOLA LETTURA e non decide mai nulla al posto dell'operatore. L'LLM
collegato qui non genera mai un fatto da solo: viene usato solo per due
cose, entrambe a valle delle query reali sul database che restano in
vicky.py:

1. classifica_intento() — capire meglio a quale tipo di domanda appartiene
   una frase, quando il riconoscimento a parole chiave non basta (varianti,
   sinonimi, domande formulate in modo diverso dagli esempi). Non risponde
   mai nel merito: instrada soltanto verso le funzioni esistenti in
   vicky.py, che restano le uniche a interrogare il database.
2. fraseggia() — riformulare in italiano naturale una risposta i cui fatti
   sono GIA' stati recuperati dal database dalle funzioni di vicky.py
   (mai il contrario: l'LLM non vede mai la domanda da solo senza i dati
   già calcolati quando deve fraseggiare).

Se la chiamata fallisce per qualunque motivo — GROQ_API_KEY non
configurata, rete assente, risposta malformata — entrambe le funzioni
ritornano None, silenziosamente: chi chiama qui (vicky.py) torna al
comportamento di sempre (dispatch a parole chiave, risposta col template
originale, già completo e corretto). Mai un'eccezione che risale fino
all'operatore, mai una domanda che smette di funzionare perché un servizio
esterno gratuito è irraggiungibile o ha esaurito la quota.

Provider: Groq (https://groq.com/), API compatibile OpenAI, piano gratuito
verificato via ricerca web il 2026-10-10 (nessuna carta di credito
richiesta, limiti per-minuto ampiamente sufficienti per una demo dal
vivo). Chiave letta da GROQ_API_KEY — mai hardcoded, mai esposta al
frontend: questa chiamata avviene solo lato server, il browser non vede
mai questa chiave (a differenza, per dire, della X-SDREA-Service-Key che
è un segreto server-a-server altrettanto mai esposto lato client).
"""

import json
import os

import httpx

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODEL = "llama-3.3-70b-versatile"


def _groq_chiama(messages, max_tokens=500, timeout=12.0):
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return None  # modulo non configurato — chi chiama ricade sul comportamento esistente
    try:
        r = httpx.post(
            _GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": _GROQ_MODEL, "messages": messages, "temperature": 0, "max_tokens": max_tokens},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        # Rete assente, rate limit esaurito, quota mensile finita, risposta
        # malformata: qualunque sia la causa, nessuna eccezione risale —
        # Vicky torna semplicemente al comportamento di sempre.
        return None


def classifica_intento(domanda: str, tipi_noti: list, esempi: list):
    """
    Ritorna {"tipo": ..., "struttura_citata": ...|None} oppure None se la
    chiamata fallisce o la risposta non è nel formato atteso. "tipo" è
    garantito essere uno tra tipi_noti, altrimenti ritorna None (mai un
    valore fuori dal set già gestito da vicky.py).
    """
    sistema = (
        "Sei un classificatore di intenti per un sistema di supporto decisionale per la "
        "protezione civile (letture idrometriche). Rispondi SOLO con un oggetto JSON valido, "
        f"nessun testo prima o dopo, con due campi: \"tipo\" (uno tra {tipi_noti}) e "
        "\"struttura_citata\" (il nome della struttura citata nella domanda, o null se "
        "nessuna). Non rispondere mai nel merito della domanda: il tuo unico compito è "
        "instradarla verso il tipo corretto."
    )
    contenuto = _groq_chiama([
        {"role": "system", "content": sistema},
        {"role": "user", "content": f"Esempi di domande gestite: {esempi}\n\nDomanda da classificare: {domanda}"},
    ], max_tokens=150)
    if not contenuto:
        return None
    try:
        testo = contenuto.strip()
        if testo.startswith("```"):
            testo = testo.strip("`")
            if testo.lower().startswith("json"):
                testo = testo[4:]
        dati = json.loads(testo.strip())
        if dati.get("tipo") not in tipi_noti:
            return None
        return dati
    except Exception:
        return None


def fraseggia(domanda: str, dati_grezzi: dict):
    """
    Riformula in italiano naturale SOLO i fatti già presenti in
    dati_grezzi (il dict già costruito da una delle funzioni rispondi_*
    in vicky.py, interrogando davvero il database). Ritorna None se la
    chiamata fallisce — in quel caso chi chiama usa semplicemente
    dati_grezzi["risposta"], il template originale, senza perdita
    d'informazione: questa funzione è un arricchimento, mai una
    dipendenza che blocca la risposta.
    """
    sistema = (
        "Riformula la risposta qui sotto in italiano naturale e colloquiale, adatta a un "
        "operatore di sala operativa durante un'emergenza idraulica: breve, chiara, diretta, "
        "senza preamboli. USA SOLO i fatti e i numeri presenti nei dati forniti — non "
        "aggiungere, stimare o inventare alcun dato, nome o numero che non sia già "
        "presente. Se i dati indicano che un'informazione manca, dillo altrettanto "
        "chiaramente nella riformulazione, non ometterlo."
    )
    contenuto = _groq_chiama([
        {"role": "system", "content": sistema},
        {"role": "user", "content": f"Domanda originale dell'operatore: {domanda}\n\nDati reali su cui basare la risposta:\n{json.dumps(dati_grezzi, ensure_ascii=False, default=str)}"},
    ], max_tokens=400)
    return contenuto.strip() if contenuto else None
