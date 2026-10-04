import json
from pathlib import Path

from immo.connectors.cbre import CbreConnector


FIXTURES = Path(__file__).parent / "fixtures" / "cbre"


def test_commerce_en_vente() -> None:
    url = "https://immobilier.cbre.fr/offre/a-vendre/commerces/01170/cessy/40197010.aspx"
    item = next(CbreConnector().parse_page((FIXTURES / "commerce.html").read_text(encoding="utf-8"), url))
    assert item["id"] == "40197010" and item["reference_annonce"] == "04_01_97010"
    assert item["price"] == 306_000 and item["surface"] == 165
    assert item["zipcode"] == "01170" and item["city"] == "Cessy"
    assert item["type_hint"] == "vente local_commercial"
    assert "Local commercial à vendre" in item["body"]


def test_prix_au_m2_non_pris_pour_un_prix() -> None:
    url = "https://immobilier.cbre.fr/offre/a-vendre/bureaux/01120/montluel/136160.aspx"
    item = next(CbreConnector().parse_page((FIXTURES / "bureaux-prix-m2.html").read_text(encoding="utf-8"), url))
    details = json.loads(item["details_json"])
    assert item["price"] is None and details["Prix au m²"] == 2300
    assert item["type_hint"] == "vente bureau" and item["surface"] == 925


def test_filtre() -> None:
    keep = CbreConnector().url_filter
    assert keep("https://immobilier.cbre.fr/offre/a-vendre/activites/01360/balan/174122.aspx")
    assert not keep("https://immobilier.cbre.fr/offre/a-louer/bureaux/75008/paris/1.aspx")
