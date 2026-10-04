import gzip
import json
from pathlib import Path

from immo.connectors.era import EraConnector

FIXTURES = Path(__file__).parent / "fixtures" / "era"


def _page(name: str) -> str:
    return gzip.decompress((FIXTURES / name).read_bytes()).decode("utf-8")


def test_era_building_from_angular_state() -> None:
    item = next(EraConnector().parse_page(_page("immeuble-537257.html.gz"), "https://www.eraimmobilier.com/annonces/537257"))
    assert item["id"] == "537257" and item["reference_annonce"] == "12017"
    assert item["type_hint"] == "immeuble"
    assert item["price"] == 92200 and item["surface"] == 167 and item["land_surface"] == 100
    assert item["zipcode"] == "86100" and item["city"] == "Châtellerault"
    assert item["dpe"] == "F" and item["ges"] == "F"
    assert float(item["lat"]) == 46.8153118045
    details = json.loads(item["details_json"])
    assert details["Code INSEE"] == "86066"
    assert details["État"] == "Travaux à prévoir"
    assert details["Prix net vendeur"] == 85000.0
    assert item["image_count"] == 14


def test_era_apartment() -> None:
    item = next(EraConnector().parse_page(_page("appartement-577980.html.gz"), "https://www.eraimmobilier.com/annonces/577980"))
    assert item["type_hint"] == "appartement"
    assert item["zipcode"] == "02100"
    assert item["published_at"] == "2026-09-11"


def test_era_skips_rentals() -> None:
    html = _page("appartement-577980.html.gz").replace('"type_annonce":"Vente"', '"type_annonce":"Location"')
    assert list(EraConnector().parse_page(html, "https://www.eraimmobilier.com/annonces/577980")) == []
