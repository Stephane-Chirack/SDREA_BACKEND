"""
Test del parsing di app/allerta_meteo_client.py — non richiede rete: usa
come fixture le risposte REALI catturate interrogando dal vivo
https://allertameteo.regione.emilia-romagna.it il 22/09/2026 (vedi
docstring del modulo client per i dettagli), non dati inventati.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.allerta_meteo_client import parse_stato_allerta, parse_bollettino_monitoraggio

# Catturato dal vivo il 22/09/2026 da GET /o/get-stato-allerta (troncato
# alle zone effettivamente osservate nella risposta reale).
FIXTURE_STATO_ALLERTA_REALE = {
    "dataEmissione": "Sep 22, 2026 12:01:40 PM",
    "titolo": "Bollettino di vigilanza 144/2026 valido dalle 00:00 del 23/09/2026: nessuna allerta.",
    "link": "/documents/20181/4744740/bollettino144_2026.pdf",
    "dataInizio": "Sep 23, 2026 12:00:00 AM",
    "dataFine": "Sep 24, 2026 12:00:00 AM",
    "descrizionemeteo": "Per mercoledì 23 settembre non sono previsti fenomeni significativi ai fini dell'allertamento.",
    "allerte": {
        "H1": {"idraulica": "green", "idrogeologica": "green", "temperature_estreme": "green", "temporali": "green", "vento": "green"},
        "G1": {"idraulica": "green", "idrogeologica": "green", "temperature_estreme": "green", "temporali": "green", "vento": "green"},
        "H2": {"idraulica": "green", "idrogeologica": "green", "temperature_estreme": "green", "temporali": "green", "vento": "green"},
        "D2": {"idraulica": "green", "idrogeologica": "green", "mareggiate": "green", "stato_mare": "green", "temperature_estreme": "green", "temporali": "green", "vento": "green"},
        "B2": {"idraulica": "green", "idrogeologica": "green", "mareggiate": "green", "stato_mare": "green", "temperature_estreme": "green", "temporali": "green", "vento": "green"},
    },
}

# Catturato dal vivo il 22/09/2026 da GET /o/get-bollettino-monitoraggio
# (troncato al primo bacino della risposta reale, SENIO).
FIXTURE_BOLLETTINO_REALE = {
    "010/2025/04ULTIMO": {
        "bacino": [
            {
                "comuni": ["ALFONSINE(RA)", "BAGNACAVALLO(RA)", "BRISIGHELLA(RA)", "CASOLA VALSENIO(RA)", "CASTEL BOLOGNESE(RA)", "COTIGNOLA(RA)", "FAENZA(RA)", "FUSIGNANO(RA)", "LUGO(RA)", "RIOLO TERME(RA)", "SOLAROLO(RA)"],
                "nome_bacino": "SENIO",
                "province": ["RA"],
                "sottobacino": [
                    {"colmo_previsto": {"dataOra": None, "valore": None}, "nomeSottobacino": "Fusignano", "osservazione": {"dataOra": "07:45", "valore": "4.06"}},
                    {"colmo_previsto": {"dataOra": None, "valore": None}, "nomeSottobacino": "Tebano", "osservazione": {"dataOra": "07:45", "valore": "0.82"}},
                    {"colmo_previsto": {"dataOra": None, "valore": None}, "nomeSottobacino": "Alfonsine", "osservazione": {"dataOra": "07:45", "valore": "4.6"}},
                    {"colmo_previsto": {"dataOra": None, "valore": None}, "nomeSottobacino": "Casola Valsenio", "osservazione": {"dataOra": "07:45", "valore": "-1.62"}},
                    {"colmo_previsto": {"dataOra": None, "valore": None}, "nomeSottobacino": "Castelbolognese", "osservazione": {"dataOra": "07:45", "valore": "-0.08"}},
                    {"colmo_previsto": {"dataOra": None, "valore": None}, "nomeSottobacino": "Cotignola", "osservazione": {"dataOra": "07:45", "valore": "8.02"}},
                ],
            }
        ],
        "fineMonitoraggio": "Nov 1, 2025 08:00:00 AM",
        "inizioMonitoraggio": "Oct 31, 2025 08:00:00 AM",
        "note": "Nella giornata di oggi la piena di Reno si propagherà nel tratto arginato di valle con livelli al colmo inferiori alle soglie 2. Nella giornata di oggi non si prevedono ulteriori precipitazioni.",
    }
}


def test_parse_stato_allerta_righe_e_colori():
    righe = parse_stato_allerta(FIXTURE_STATO_ALLERTA_REALE)
    # 5 fenomeni * 3 zone (H1, G1, H2) + 7 fenomeni * 2 zone (D2, B2) = 15 + 14 = 29
    assert len(righe) == 29
    zona_h1_idraulica = [r for r in righe if r["zona"] == "H1" and r["fenomeno"] == "idraulica"]
    assert len(zona_h1_idraulica) == 1
    assert zona_h1_idraulica[0]["colore"] == "verde"
    assert zona_h1_idraulica[0]["colore_raw"] == "green"
    assert zona_h1_idraulica[0]["titolo_bollettino"].startswith("Bollettino di vigilanza 144/2026")
    assert all(r["fonte"] for r in righe)


def test_parse_stato_allerta_mappa_tutti_i_colori():
    payload = {
        "dataEmissione": "x", "titolo": "t", "link": None, "dataInizio": None, "dataFine": None,
        "allerte": {"Z1": {"idraulica": "red", "idrogeologica": "orange", "vento": "yellow", "temporali": "green", "boh": None}},
    }
    righe = parse_stato_allerta(payload)
    colori = {r["fenomeno"]: r["colore"] for r in righe}
    assert colori["idraulica"] == "rosso"
    assert colori["idrogeologica"] == "arancione"
    assert colori["vento"] == "giallo"
    assert colori["temporali"] == "verde"
    assert colori["boh"] == "sconosciuto"


def test_parse_stato_allerta_scarta_zone_malformate():
    payload = {"dataEmissione": "x", "titolo": "t", "allerte": {"Z1": "non-un-dict", "Z2": {"idraulica": "green"}}}
    righe = parse_stato_allerta(payload)
    assert len(righe) == 1
    assert righe[0]["zona"] == "Z2"


def test_parse_bollettino_monitoraggio_righe_reali():
    righe = parse_bollettino_monitoraggio(FIXTURE_BOLLETTINO_REALE)
    assert len(righe) == 6
    fusignano = next(r for r in righe if r["nome_sottobacino"] == "Fusignano")
    assert fusignano["nome_bacino"] == "SENIO"
    assert fusignano["livello_osservato"] == 4.06
    assert fusignano["ora_osservazione_raw"] == "07:45"
    assert fusignano["bollettino_id"] == "010/2025/04ULTIMO"
    assert fusignano["livello_colmo_previsto"] is None  # colmo_previsto.valore era null nella risposta reale

    casola = next(r for r in righe if r["nome_sottobacino"] == "Casola Valsenio")
    assert casola["livello_osservato"] == -1.62  # livelli negativi sono legittimi (sotto lo zero idrometrico)


def test_parse_bollettino_monitoraggio_scarta_sottobacini_senza_nome():
    payload = {"B/1": {"bacino": [{"nome_bacino": "X", "sottobacino": [{"nomeSottobacino": None, "osservazione": {}}, {"nomeSottobacino": "Y", "osservazione": {"valore": "1.0", "dataOra": "08:00"}}]}]}}
    righe = parse_bollettino_monitoraggio(payload)
    assert len(righe) == 1
    assert righe[0]["nome_sottobacino"] == "Y"


def test_parse_bollettino_monitoraggio_valore_non_numerico_diventa_none():
    payload = {"B/1": {"bacino": [{"nome_bacino": "X", "sottobacino": [{"nomeSottobacino": "Y", "osservazione": {"valore": "n.d.", "dataOra": "08:00"}}]}]}}
    righe = parse_bollettino_monitoraggio(payload)
    assert righe[0]["livello_osservato"] is None
