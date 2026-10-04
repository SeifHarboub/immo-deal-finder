import json
from pathlib import Path

from immo.connectors.placedescommerces import PlaceDesCommercesConnector, parse_detail
from immo.finance import extract


FIXTURES = Path(__file__).parent / "fixtures" / "placedescommerces"


def test_fiche_avec_elements_chiffres() -> None:
    item = parse_detail((FIXTURES / "detail-financials.html").read_text(encoding="utf-8"), "https://x.test/")
    details = json.loads(item["details_json"])
    assert item["id"] == "1359326" and item["reference_annonce"] == "C1359326"
    assert item["url"].endswith("vente-bar-tabac-presse-en-plein-centre-ville-doree-danjou-49270,c1359326_fr_")
    assert item["price"] == 180_000 and item["zipcode"] == "49270" and item["city"] == "Orée-d'Anjou"
    assert item["type_hint"] == "vente fonds_commerce" and item["surface"] == 80
    assert details["Chiffre d'affaires"] == 243_000 and details["EBE"] == 24_000
    assert details["Résultat net"] == 11_000 and details["Année des chiffres"] == 2026
    assert details["Loyer du bail annuel"] == 766 * 12 and "Loyer annuel" not in details
    assert details["Secteur"] == "Bar / Tabac" and details["Licence IV"] == "oui"
    finance = extract(item["name"], item["body"], item["details_json"], item["price"], "fonds_commerce")
    assert finance.chiffre_affaires == 243_000 and finance.ebe == 24_000 and finance.segment == "fonds"


def test_fiche_simple() -> None:
    item = parse_detail((FIXTURES / "detail-simple.html").read_text(encoding="utf-8"), "https://x.test/")
    assert item["price"] == 46_000 and item["published_at"] == "2026-10-02"
    assert json.loads(item["details_json"])["Places en salle"] == 40


def test_balayage_progressif(tmp_path, monkeypatch) -> None:
    state = tmp_path / "pdc.json"
    monkeypatch.setenv("PLACEDESCOMMERCES_STATE_FILE", str(state))
    monkeypatch.setenv("PLACEDESCOMMERCES_RECENT_PAGES", "1")
    monkeypatch.setenv("PLACEDESCOMMERCES_SWEEP_PAGES_PER_RUN", "2")
    page = (FIXTURES / "list-last-page.html").read_text(encoding="utf-8")
    requested: list[str] = []
    connector = PlaceDesCommercesConnector()

    def fake_fetch(url):
        requested.append(url)
        return page, url

    monkeypatch.setattr(connector, "_fetch_detail", fake_fetch)
    urls = list(connector._sitemap_urls(connector.sitemap_url))
    assert len(urls) == 9 and all(",c" in url and "?" not in url for url in urls)
    assert json.loads(state.read_text())["next_page"] == 3 and json.loads(state.read_text())["last_page"] == 2474
    list(connector._sitemap_urls(connector.sitemap_url))
    assert requested[-1].endswith("page=4")  # reprise à la page mémorisée
    state.write_text(json.dumps({"next_page": 2474, "last_page": 2474}))
    list(connector._sitemap_urls(connector.sitemap_url))
    assert json.loads(state.read_text())["next_page"] == 1  # fin du catalogue : on reboucle
