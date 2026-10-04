import gzip
import json
from pathlib import Path

import pytest

from immo.connectors.safti import SaftiConnector

FIXTURES = Path(__file__).parent / "fixtures" / "safti"


def _page(name: str) -> str:
    return gzip.decompress((FIXTURES / name).read_bytes()).decode("utf-8")


def test_safti_house_from_flight_payload() -> None:
    url = "https://www.safti.fr/annonces/achat/maison/andilly-95580/1619518"
    item = next(SaftiConnector().parse_page(_page("maison-1619518.html.gz"), url))
    assert item["id"] == "1619518"
    assert item["type_hint"] == "maison"
    assert item["price"] == 670000 and item["surface"] == 218 and item["land_surface"] == 683
    assert item["rooms"] == 10 and item["bedrooms"] == 7
    assert item["zipcode"] == "95580" and item["city"] == "ANDILLY"
    assert item["dpe"] == "B" and item["ges"] == "B"
    assert item["lat"] == pytest.approx(49.0098)
    # La description complète vient du bloc RSC `$3a`, avec ses retours à la ligne.
    assert item["body"].startswith("Andilly – Idéal Maison familiale") and "\n" in item["body"]
    assert item["image_count"] == 13
    details = json.loads(item["details_json"])
    assert details["Année de construction"] == 1981
    assert details["Honoraires"] == 23450.0
    assert "Sous compromis" not in details


def test_safti_apartment_copro_and_tax() -> None:
    url = "https://www.safti.fr/annonces/achat/appartement/villeneuve-tolosane-31270/1631631"
    item = next(SaftiConnector().parse_page(_page("appartement-1631631.html.gz"), url))
    details = json.loads(item["details_json"])
    assert item["type_hint"] == "appartement"
    assert details["Charges annuelles de copropriété"] == 1200.0
    assert details["Taxe foncière"] == 871.0
    assert details["Nombre de lots"] == 70
    assert item["seller_type"] == "pro"


def test_safti_ignores_pages_without_listing() -> None:
    assert list(SaftiConnector().parse_page("<html></html>", "https://www.safti.fr/x")) == []
