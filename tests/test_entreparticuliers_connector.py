import gzip
import json
from pathlib import Path

from immo.connectors.entreparticuliers import EntreParticuliersConnector

FIXTURES = Path(__file__).parent / "fixtures" / "entreparticuliers"
BASE = "https://www.entreparticuliers.com/annonces-immobilieres/maison/vente/"


def _page(name: str) -> str:
    return gzip.decompress((FIXTURES / name).read_bytes()).decode("utf-8")


def test_entreparticuliers_private_house() -> None:
    url = BASE + "belle-eglise-60540/maison-atypique-de-160-m2-a-belle-eglise/ref-22633997"
    item = next(EntreParticuliersConnector().parse_page(_page("maison-22633997.html.gz"), url))
    assert item["id"] == "22633997" and item["type_hint"] == "maison"
    assert item["price"] == "310000" and item["surface"] == 160 and item["rooms"] == 5
    assert item["zipcode"] == "60540" and item["city"] == "Belle-Église"
    assert item["seller_type"] == "private"
    assert item["dpe"] == "C" and item["ges"] == "C"
    assert item["image_count"] == 3
    assert json.loads(item["details_json"])["Équipements"] == ["Jardin"]


def test_entreparticuliers_drops_listings_older_than_a_year() -> None:
    url = BASE + "obernai-67210/maison-a-vendre-sur-9-5-de-terrain/ref-13585814"
    assert list(EntreParticuliersConnector().parse_page(_page("ancienne-13585814.html.gz"), url)) == []
