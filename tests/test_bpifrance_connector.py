import json
from pathlib import Path

from immo.connectors.bpifrance import BpifranceConnector, amount, keep_url
from immo.finance import extract


FIXTURES = Path(__file__).parent / "fixtures" / "bpifrance"
BASE = "https://reprise-entreprise.bpifrance.fr"


def test_entreprise() -> None:
    url = f"{BASE}/annonce/bar-brasserie-tabac-vannes-637743"
    item = next(BpifranceConnector().parse_page((FIXTURES / "business-637743.html").read_text(encoding="utf-8"), url))
    details = json.loads(item["details_json"])
    assert item["id"] == "637743" and item["type_hint"] == "vente fonds_commerce"
    assert item["name"] == "Bar - brasserie - tabac vannes"
    assert item["price"] == 580_000 and item["zipcode"] is None
    assert item["published_at"] == "2026-07-18"
    assert details["Département"] == "56" and details["Secteur"] == "Restauration et Tourisme"
    assert details["Chiffre d'affaires"] == 7_500_000 and "EBE" not in details  # « 0 k€ » ignoré
    assert details["Effectif"] == 4 and details["Partenaire"] == "cession-pme"
    finance = extract(item["name"], item["body"], item["details_json"], item["price"], "fonds_commerce")
    assert finance.chiffre_affaires == 7_500_000 and finance.segment == "fonds"


def test_local_en_vente() -> None:
    url = f"{BASE}/locaux/annonce-locaux/vente-de-murs-de-boutique-yvelines-78-01b0f729b7c9afba1bbdeffb11a6ec7f"
    item = next(BpifranceConnector().parse_page((FIXTURES / "premises-sale.html").read_text(encoding="utf-8"), url))
    details = json.loads(item["details_json"])
    assert item["type_hint"] == "vente local_commercial" and item["surface"] == 130
    assert item["price"] is None and details["Département"] == "78"
    finance = extract(item["name"], item["body"], item["details_json"], 530_000, "local_commercial")
    assert finance.loyer_annuel_declare == 48_000


def test_filtres_et_montants() -> None:
    assert keep_url(f"{BASE}/annonce/specialiste-du-forage-649840")
    assert keep_url(f"{BASE}/locaux/annonce-locaux/murs-commerciaux-82-m2-a-monteux-6338cbd932f7da20d3351bc7dfe5b238")
    assert not keep_url(f"{BASE}/locaux/annonce-locaux/location-local-commercial-var-83-01930897a285ce921950231d636a802f")
    assert not keep_url(f"{BASE}/tracking/annonce/click/641194/site/0")
    assert amount("Prix : 580 k€") == 580_000
    assert amount("CA : 1,2 M€") == 1_200_000
    assert amount("350 000 €") == 350_000
    assert amount("NC") is None
