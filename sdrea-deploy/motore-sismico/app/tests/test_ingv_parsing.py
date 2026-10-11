"""
Test del parser del formato text INGV (vedi app/ingv_client.py) contro
una fixture nel formato pubblicato da INGV. Non richiede rete: verifica
che il parsing sia corretto indipendentemente dal fatto che questa
sandbox possa raggiungere webservices.ingv.it o meno.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.ingv_client import parse_ingv_text

FIXTURE = """#EventID|Time|Latitude|Longitude|Depth/Km|Author|Catalog|Contributor|ContributorID|MagType|Magnitude|MagAuthor|EventLocationName|EventType
12345678|2026-09-20T14:32:10.123456|44.8500|11.6200|8.4|INGV|ESM|INGV|smi:webservices.ingv.it/12345678|ML|3.4|INGV|3 km NE Ferrara (FE)|earthquake
12345679|2026-09-21T02:11:05.000000|44.9000|11.5000|10.0|INGV|ESM|INGV|smi:webservices.ingv.it/12345679|ML|2.1|INGV|5 km W Bondeno (FE)|earthquake
"""

MALFORMED_FIXTURE = """#EventID|Time|Latitude|Longitude|Depth/Km|Author
riga|troppo|corta
"""


def test_parses_two_events():
    eventi = parse_ingv_text(FIXTURE)
    assert len(eventi) == 2, f"attesi 2 eventi, trovati {len(eventi)}"

    e1 = eventi[0]
    assert e1["ingv_event_id"] == "12345678"
    assert e1["magnitudo"] == 3.4
    assert e1["lat_epicentro"] == 44.85
    assert e1["lon_epicentro"] == 11.62
    assert e1["profondita_km"] == 8.4
    assert e1["luogo"] == "3 km NE Ferrara (FE)"
    assert e1["fonte"] == "INGV"
    assert e1["timestamp"].year == 2026 and e1["timestamp"].month == 9 and e1["timestamp"].day == 20

    e2 = eventi[1]
    assert e2["ingv_event_id"] == "12345679"
    assert e2["magnitudo"] == 2.1

    print("test_parses_two_events: OK")


def test_skips_malformed_rows_without_crashing():
    eventi = parse_ingv_text(MALFORMED_FIXTURE)
    assert eventi == [], f"attesa lista vuota per righe malformate, trovato {eventi}"
    print("test_skips_malformed_rows_without_crashing: OK")


def test_empty_input():
    assert parse_ingv_text("") == []
    assert parse_ingv_text("   \n  \n") == []
    print("test_empty_input: OK")


if __name__ == "__main__":
    test_parses_two_events()
    test_skips_malformed_rows_without_crashing()
    test_empty_input()
    print("ALL INGV PARSING TESTS PASSED")
