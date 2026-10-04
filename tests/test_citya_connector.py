import gzip
import json
from pathlib import Path

from immo.connectors.citya import CityaConnector

FIXTURES = Path(__file__).parent / "fixtures" / "citya"


def _page(name: str) -> str:
    return gzip.decompress((FIXTURES / name).read_bytes()).decode("utf-8")


def test_citya_house() -> None:
    url = "https://www.citya.com/annonces/vente/maison/olivet-45160/TMAI287-955079"
    item = next(CityaConnector().parse_page(_page("maison-TMAI287-955079.html.gz"), url))
    assert item["id"] == "TMAI287-955079" and item["type_hint"] == "maison"
    assert item["price"] == 383000 and item["surface"] == 85.18 and item["land_surface"] == 732
    assert item["zipcode"] == "45160" and item["city"] == "Olivet"
    assert item["dpe"] == "D" and item["ges"] == "B"
    assert item["lat"] == "47.854875"
    assert item["seller_name"] == "Citya République"
    details = json.loads(item["details_json"])
    assert details["Taxe foncière"] == 2090 and details["Année de construction"] == 1975
    assert details["DPE consommation"] == 238
    assert item["image_count"] == 12


def test_citya_prefers_copro_quote_part_over_building_total() -> None:
    url = "https://www.citya.com/annonces/vente/appartement/miramas-13140/TAPP952478"
    details = json.loads(next(CityaConnector().parse_page(_page("appartement-TAPP952478.html.gz"), url))["details_json"])
    assert details["Charges annuelles de copropriété"] == 1300
    assert details["Charges annuelles affichées"] == 156000
    assert details["Nombre de lots"] == 60 and details["Sous compromis"] == "non"


def test_citya_url_filter_keeps_only_residential_sales() -> None:
    keep = CityaConnector().url_filter
    assert keep("https://www.citya.com/annonces/vente/appartement/tours-37000/TAPP1-2")
    assert not keep("https://www.citya.com/annonces/vente/parking/tours-37000/TPAR1-2")
    assert not keep("https://www.citya.com/annonces/vente/appartement")
