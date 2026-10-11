"""
Test del parsing ARPA (vedi app/arpa_client.py) — non richiede rete: verifica
che parse_arpa_json/parse_arpa_csv siano corretti indipendentemente dal
fatto che ARPA_IDRO_BASE_URL sia configurato o meno in questa sessione.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.arpa_client import parse_arpa_json, parse_arpa_csv
from app.idrometria import deriva_livello_allerta, struttura_e_rilevante, stima_impatto

FIXTURE_JSON = [
    {
        "codice_stazione": "16000200",
        "nome_stazione": "Ferrara Pontelagoscuro",
        "corso_d_acqua": "Po",
        "lat": 44.8886, "lon": 11.6103,
        "livello_idrometrico_m": 4.2,
        "soglia1_ordinaria_m": 3.0,
        "soglia2_moderata_m": 4.0,
        "soglia3_elevata_m": 5.0,
        "timestamp": "2026-09-22T08:00:00",
        "fonte": "ARPAE",
    },
    {
        "codice_stazione": "16000201",
        "corso_d_acqua": "Reno",
        "lat": 44.5, "lon": 11.3,
        "livello_idrometrico_m": 1.1,
        "timestamp": "2026-09-22T08:00:00",
    },
]

FIXTURE_CSV = """codice_stazione,nome_stazione,corso_d_acqua,lat,lon,livello_idrometrico_m,soglia1_ordinaria_m,soglia2_moderata_m,soglia3_elevata_m,timestamp,fonte
16000200,Ferrara Pontelagoscuro,Po,44.8886,11.6103,4.2,3.0,4.0,5.0,2026-09-22T08:00:00,ARPAE
"""

MALFORMED_JSON = [{"codice_stazione": "x"}]  # manca lat/lon/livello/timestamp


def test_parses_json_two_letture():
    eventi = parse_arpa_json(FIXTURE_JSON)
    assert len(eventi) == 2, f"attese 2 letture, trovate {len(eventi)}"
    e1 = eventi[0]
    assert e1["codice_stazione"] == "16000200"
    assert e1["corso_d_acqua"] == "Po"
    assert e1["livello_idrometrico_m"] == 4.2
    assert e1["soglia3_elevata_m"] == 5.0
    assert e1["lettura_id"] == "16000200@2026-09-22T08:00:00"
    print("test_parses_json_two_letture: OK")


def test_parses_csv():
    eventi = parse_arpa_csv(FIXTURE_CSV)
    assert len(eventi) == 1
    assert eventi[0]["nome_stazione"] == "Ferrara Pontelagoscuro"
    print("test_parses_csv: OK")


def test_skips_malformed_rows():
    eventi = parse_arpa_json(MALFORMED_JSON)
    assert eventi == [], f"attesa lista vuota, trovato {eventi}"
    print("test_skips_malformed_rows: OK")


def test_deriva_livello_allerta():
    assert deriva_livello_allerta(4.2, 3.0, 4.0, 5.0) == "arancione"
    assert deriva_livello_allerta(5.5, 3.0, 4.0, 5.0) == "rosso"
    assert deriva_livello_allerta(1.0, 3.0, 4.0, 5.0) == "verde"
    assert deriva_livello_allerta(1.0, None, None, None) == "sconosciuto"
    print("test_deriva_livello_allerta: OK")


def test_struttura_e_rilevante():
    assert struttura_e_rilevante("Po", "Po", 10.0) is True
    assert struttura_e_rilevante("Reno", "Po", 10.0) is False
    assert struttura_e_rilevante(None, "Po", 10.0) is True  # dato mancante: non esclude
    assert struttura_e_rilevante("Po", "Po", 999.0) is False  # troppo lontano
    print("test_struttura_e_rilevante: OK")


def test_stima_impatto():
    impatto = stima_impatto(4.2, 3.0, 4.0, 5.0, quota_slm_m=3.0)
    assert impatto["livello_allerta"] == "arancione"
    assert impatto["superamento_normalizzato"] > 0
    assert impatto["stima_grezza_senza_soglie_ufficiali"] is False
    assert impatto["rischio_diretto_struttura_per_quota"] is True  # 4.2 - 3.0 >= -1.5
    print("test_stima_impatto: OK")


if __name__ == "__main__":
    test_parses_json_two_letture()
    test_parses_csv()
    test_skips_malformed_rows()
    test_deriva_livello_allerta()
    test_struttura_e_rilevante()
    test_stima_impatto()
    print("ALL ARPA/IDROMETRIA TESTS PASSED")
