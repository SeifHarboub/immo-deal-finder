import gzip
import json
from pathlib import Path

from immo.connectors.century21 import Century21Connector

FIXTURES = Path(__file__).parent / "fixtures" / "century21"


def _page(name: str) -> str:
    return gzip.decompress((FIXTURES / name).read_bytes()).decode("utf-8")


def test_century21_house() -> None:
    url = "https://www.century21.fr/trouver_logement/detail/15652938098/"
    item = next(Century21Connector().parse_page(_page("maison-15652938098.html.gz"), url))
    assert item["id"] == "15652938098" and item["reference_annonce"] == "9263"
    assert item["type_hint"] == "maison"
    assert item["price"] == 479000 and item["surface"] == 242.5 and item["land_surface"] == 3265
    assert item["rooms"] == 7 and item["bedrooms"] == 5
    assert item["zipcode"] == "41000" and item["city"] == "Villebarou"
    assert item["seller_name"] == "CENTURY 21 Girault Immobilier"
    # 212 kWh et 29 kg CO2 : classe D (énergie) et C (climat).
    assert item["dpe"] == "D" and item["ges"] == "C"
    details = json.loads(item["details_json"])
    assert details["Taxe foncière"] == 2831 and details["Prix hors honoraires"] == 455050
    assert item["image_count"] > 100 and all("_9263_1_" in url for url in json.loads(item["images_json"]))


def test_century21_apartment_energy_class() -> None:
    url = "https://www.century21.fr/trouver_logement/detail/16715440983/"
    item = next(Century21Connector().parse_page(_page("appartement-16715440983.html.gz"), url))
    assert item["type_hint"] == "appartement" and item["zipcode"] == "60200"
    assert item["dpe"] == "E" and item["ges"] == "C"
    assert json.loads(item["details_json"])["Étage"] == "2 ème"
