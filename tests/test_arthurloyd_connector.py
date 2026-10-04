import json
from pathlib import Path

from immo.connectors.arthurloyd import ArthurLoydConnector, keep_url


FIXTURES = Path(__file__).parent / "fixtures" / "arthurloyd"
BASE = "https://www.arthur-loyd.com"


def test_local_commercial() -> None:
    url = f"{BASE}/locaux-commerciaux-vente/dom-tom/centre-martinique/le-lamentin/local-commercial-46-m2-ref-AL4580"
    item = next(ArthurLoydConnector().parse_page((FIXTURES / "local.html").read_text(encoding="utf-8"), url))
    assert item["id"] == "AL4580" and item["price"] == 162_000 and item["surface"] == 46
    assert item["zipcode"] == "97232" and item["city"] == "Le Lamentin"
    assert item["lat"] == 14.62764 and item["type_hint"] == "vente local_commercial"
    assert item["seller_name"] == "Yann NOLEO"


def test_fonds() -> None:
    url = (f"{BASE}/fonds-de-commerce-cession/nouvelle-aquitaine/vienne-86/chatellerault/"
           "fonds-de-commerce-a-vendre-emplacement-n-176-1-en-zone-commerciale-chatellerault-ref-5061")
    item = next(ArthurLoydConnector().parse_page((FIXTURES / "fonds.html").read_text(encoding="utf-8"), url))
    assert item["type_hint"] == "vente fonds_commerce" and item["price"] == 650_000
    assert json.loads(item["details_json"])["Département"] == "vienne-86"
    assert "920 000€ HT" in item["body"]


def test_filtre() -> None:
    assert keep_url(f"{BASE}/bureau-vente/pays-de-la-loire/nantes/achat-bureaux-ile-de-nantes-ref-N113GC")
    assert keep_url(f"{BASE}/bureau-vente/auvergne-rhone-alpes/rhone/lyon/lyon-6eme/bureaux-ref-905529-0V")
    assert not keep_url(f"{BASE}/bureau-location/centre-val-de-loire/orleans/orleans/bureaux-ref-932579-0L")
