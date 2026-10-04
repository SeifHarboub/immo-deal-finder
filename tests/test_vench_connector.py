import gzip
import json
from pathlib import Path

from immo.connectors.vench import VenchConnector


FIXTURES = Path(__file__).parent / "fixtures" / "vench"


def fixture(name: str) -> str:
    return gzip.decompress((FIXTURES / f"{name}.gz").read_bytes()).decode("utf-8")


def test_liste_prochaines_ventes() -> None:
    urls, pages = VenchConnector()._extract(fixture("prochaines.html"))
    assert len(urls) == 12 and pages == 49
    assert urls[0].startswith("https://www.vench.fr/vente-")


def test_vente_a_venir() -> None:
    [item] = VenchConnector().parse_page(fixture("detail_avenir.html"),
                                         "https://www.vench.fr/vente-166525-un-appartement-toulon.html")
    details = json.loads(item["details_json"])
    assert item["price"] == "30000.0" and item["surface"] == "66.06" and item["zipcode"] == "83000"
    assert item["date_vente"] == "2026-11-12T15:00:00" and item["lat"] == "43.127469"
    assert details["Tribunal"] == "Tribunal judiciaire de TOULON" and details["Occupation"] == "occupé"
    assert details["Faculté de baisse"] == "Oui" and details["Clé enchère"] == "toulon|2026-11-12|toulon|30000"


def test_vente_terminee_sans_resultat_public() -> None:
    [item] = VenchConnector().parse_page(fixture("detail_terminee.html"),
                                         "https://www.vench.fr/vente-166534-un-appartement-de-type-4-marseille.html")
    assert json.loads(item["details_json"])["Statut"] == "non communiqué"
    assert item["prix_adjuge"] is None and item["rooms"] == "4"
