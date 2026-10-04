import gzip
import json
from pathlib import Path

from immo.connectors.figaro import FigaroConnector

FIXTURES = Path(__file__).parent / "fixtures" / "figaro"
URL = "https://immobilier.lefigaro.fr/annonces/annonce-100000013.html"


def _page() -> str:
    return gzip.decompress((FIXTURES / "annonce-100000013.html.gz").read_bytes()).decode("utf-8")


def test_figaro_sale_from_nuxt_data() -> None:
    item = next(FigaroConnector().parse_page(_page(), URL))
    assert item["id"] == "100000013" and item["type_hint"] == "maison"
    assert item["price"] == 149950 and item["surface"] == 35 and item["land_surface"] == 235
    assert item["rooms"] == 2 and item["bedrooms"] == 1
    assert item["zipcode"] == "77100" and item["city"] == "Mareuil-lès-Meaux"
    assert item["dpe"] == "D" and item["ges"] == "D"
    assert item["seller_name"] == "L'ADRESSE - Meaux"
    assert item["published_at"] == "2026-03-25T04:44:36"
    assert item["image_count"] == 9
    details = json.loads(item["details_json"])
    assert [step["value"] for step in details["Historique des prix"]] == [159750, 154950, 149950]
    assert details["Code INSEE"] == "77276"
    assert details["Taxe foncière"] == 381
    assert details["Précision cartographique"] == "approximative"


def test_figaro_skips_rentals_and_new_programmes() -> None:
    head, nuxt = _page().split("__NUXT_DATA__", 1)
    # devalue déduplique les chaînes : un seul "vente" dans l'état Nuxt.
    assert nuxt.count('"vente"') == 1
    rental = head + "__NUXT_DATA__" + nuxt.replace('"vente"', '"location"')
    assert list(FigaroConnector().parse_page(rental, URL)) == []
