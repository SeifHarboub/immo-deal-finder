import gzip
import json
from pathlib import Path

from immo.connectors.proprietesprivees import ProprietesPriveesConnector, _JsReader, nuxt_trade

FIXTURES = Path(__file__).parent / "fixtures" / "proprietesprivees"


def _page(name: str) -> str:
    return gzip.decompress((FIXTURES / name).read_bytes()).decode("utf-8")


def test_js_reader_handles_nuxt_literals() -> None:
    value = _JsReader('{a:x,"b":[1,2.5,"\\u002Fp\\u00e9"],c:void 0,d:Array(2),e:!0}'.replace(",e:!0", ""), {"x": True}).value()
    assert value == {"a": True, "b": [1, 2.5, "/pé"], "c": None, "d": []}


def test_proprietesprivees_house_from_nuxt_function() -> None:
    url = "https://www.proprietes-privees.com/annonces/457878AURB"
    trade = nuxt_trade(_page("457878AURB.html.gz"))
    assert trade["prestation"] == "buy" and trade["salePrice"] == "208000.00"
    item = next(ProprietesPriveesConnector().parse_page(_page("457878AURB.html.gz"), url))
    assert item["id"] == "457878AURB" and item["type_hint"] == "maison"
    assert item["price"] == 208000 and item["surface"] == 111 and item["land_surface"] == 347
    assert item["rooms"] == 6 and item["bedrooms"] == 4
    assert item["zipcode"] == "18000" and item["city"] == "Bourges"
    assert item["dpe"] == "A" and item["ges"] == "A"
    assert item["image_count"] == 18
    details = json.loads(item["details_json"])
    assert details["Précision cartographique"] == "approximative"
    assert details["DPE consommation"] == 69


def test_proprietesprivees_skips_missing_pages() -> None:
    url = "https://www.proprietes-privees.com/annonces/445133ILD"
    assert list(ProprietesPriveesConnector().parse_page(_page("introuvable-445133ILD.html.gz"), url)) == []
