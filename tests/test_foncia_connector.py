import gzip
import json
from pathlib import Path
import re

from immo.connectors.foncia import FonciaConnector

FIXTURES = Path(__file__).parent / "fixtures" / "foncia"
URL = "https://fr.foncia.com/achat/grenoble-38/appartement/00810953.htm"


def _page(name: str) -> str:
    return gzip.decompress((FIXTURES / name).read_bytes()).decode("utf-8")


def test_foncia_apartment_from_transfer_state() -> None:
    item = next(FonciaConnector().parse_page(_page("appartement-00810953.html.gz"), URL))
    assert item["id"] == "00810953" and item["type_hint"] == "appartement"
    assert item["price"] == 125000 and item["surface"] == 69 and item["rooms"] == 3
    assert item["bedrooms"] == 2 and item["zipcode"] == "38100" and item["city"] == "Grenoble"
    assert item["dpe"] == "E" and item["ges"] == "C"
    assert round(item["lat"], 4) == 45.1785
    details = json.loads(item["details_json"])
    assert details["DPE consommation"] == 274 and details["Code INSEE"] == "38185"
    assert details["Année de construction"] == 1968
    assert item["image_count"] == 11


def test_foncia_copro_charges_are_annualised() -> None:
    url = "https://fr.foncia.com/achat/flumet-73590/appartement/00608215.htm"
    details = json.loads(next(FonciaConnector().parse_page(_page("appartement-00608215.html.gz"), url))["details_json"])
    assert details["Charges annuelles de copropriété"] == 94 * 12
    assert details["Nombre de lots"] == 39


def test_foncia_json_ld_fallback_reads_price_from_title() -> None:
    html = re.sub(r'<script id="serverApp-state".*?</script>', "", _page("appartement-00810953.html.gz"), flags=re.S)
    item = next(FonciaConnector().parse_page(html, URL))
    assert item["price"] == "125000" and item["zipcode"] == "38100" and item["surface"] == 69
