import json
from pathlib import Path

from immo.connectors.msimond import INTERMEDIATE, MichelSimondConnector, ca_bundle, parse_detail
from immo.finance import extract


FIXTURES = Path(__file__).parent / "fixtures" / "msimond"
BASE = "https://www.msimond.fr/acheter"


def _parse(name: str, url: str) -> dict:
    return parse_detail((FIXTURES / name).read_text(encoding="utf-8"), url)


def test_fonds_avec_ca_ebe() -> None:
    item = _parse("fonds.html", f"{BASE}/commerce/terminal-de-cuisson-boulangerie-patisserie-snack")
    details = json.loads(item["details_json"])
    assert item["id"] == "31817" and item["reference_annonce"] == "MPL009269"
    assert item["price"] == 605_000 and item["type_hint"] == "vente fonds_commerce"
    assert details["Chiffre d'affaires"] == 623_100 and details["EBE"] == 148_600
    assert details["Département"] == "34" and details["Secteur"] == "Snack/Restauration rapide"
    finance = extract(item["name"], item["body"], item["details_json"], item["price"], "fonds_commerce")
    assert finance.ebe == 148_600 and finance.segment == "fonds"


def test_fonds_murs_et_murs_seuls() -> None:
    both = _parse("fonds-murs.html", f"{BASE}/commerce/caveau-restaurant")
    details = json.loads(both["details_json"])
    assert both["price"] == 772_800 and details["Prix des murs"] == 627_200
    assert details["Nature"] == "Fonds de commerce + Murs"
    walls = _parse("murs.html", f"{BASE}/immobilier-entreprise/murs-ancien-hotel-nord-67")
    assert walls["price"] == 572_000 and walls["surface"] == 535
    assert walls["type_hint"] == "vente autre"  # « Vente murs de Local d'activité »


def test_filtre_et_tls() -> None:
    keep = MichelSimondConnector().url_filter
    assert keep(f"{BASE}/hotellerie/hotel-3-etoiles-annecy")
    assert not keep("https://www.msimond.fr/nous-connaitre/nos-actualites/x")
    assert not keep(f"{BASE}/commerce?page=2")
    assert INTERMEDIATE.exists()
    bundle = Path(ca_bundle()).read_text(encoding="utf-8")
    assert INTERMEDIATE.read_text(encoding="utf-8").strip() in bundle
