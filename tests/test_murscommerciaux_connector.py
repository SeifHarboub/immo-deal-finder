import json
from pathlib import Path

from immo.connectors.murscommerciaux import MursCommerciauxConnector, parse_detail
from immo.finance import extract


FIXTURES = Path(__file__).parent / "fixtures" / "murscommerciaux"
URL = "https://www.murscommerciaux.com/annonce/4357/vente-murs-commerciaux-occupes-vitrolles-340m2"


def test_murs_loues() -> None:
    item = parse_detail((FIXTURES / "detail.html").read_text(encoding="utf-8"), URL)
    details = json.loads(item["details_json"])
    assert item["id"] == "4357" and item["reference_annonce"] == "6251M"
    assert item["price"] == 2_043_600 and item["surface"] == 340
    assert item["zipcode"] == "13127" and item["city"] == "Vitrolles"
    assert item["type_hint"] == "vente local_commercial"
    assert details["Loyer annuel"] == 148_010 and details["Rentabilité brute"] == 7.2
    assert details["Occupé"] == "oui" and details["Indexation du loyer"] == "annuelle"
    finance = extract(item["name"], item["body"], item["details_json"], item["price"], "local_commercial")
    assert finance.loyer_annuel_declare == 148_010 and finance.rendement_declare == 7.2
    assert finance.bien_loue and finance.segment == "murs"


def test_pagination(monkeypatch) -> None:
    page = (FIXTURES / "list.html").read_text(encoding="utf-8")
    connector = MursCommerciauxConnector()
    calls: list[str] = []

    def fake_fetch(url):
        calls.append(url)
        return page, url  # même page : la 2e n'apporte rien de neuf, arrêt

    monkeypatch.setattr(connector, "_fetch_detail", fake_fetch)
    urls = list(connector._sitemap_urls(connector.sitemap_url))
    assert len(urls) == 8 and all("/annonce/" in url for url in urls)
    assert calls[1].endswith("limit=8,8")
