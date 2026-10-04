import gzip
import json
from pathlib import Path

from immo.connectors.avoventes import AvoventesConnector


FIXTURES = Path(__file__).parent / "fixtures" / "avoventes"


def fixture(name: str) -> str:
    return gzip.decompress((FIXTURES / f"{name}.gz").read_bytes()).decode("utf-8")


def parse(name: str, slug: str, connector=None) -> dict:
    connector = connector or AvoventesConnector()
    [item] = connector.parse_page(fixture(name), f"https://avoventes.fr/enchere/{slug}")
    return item


def test_liste_ventes_passees_et_statuts() -> None:
    connector = AvoventesConnector()
    urls, pages = connector._extract(fixture("ventes_passees.html"))
    assert len(urls) == 25 and pages == 68
    assert all(url.startswith("https://avoventes.fr/enchere/") for url in urls)
    assert connector._listing_status["https://avoventes.fr/enchere/maison-a-vuillafans"] == "retiré"


def test_adjudication_et_surenchere() -> None:
    item = parse("detail_adjuge_surenchere.html", "appartement-cave-a-marseille-13")
    details = json.loads(item["details_json"])
    assert item["price"] == "48500.0" and item["prix_adjuge"] == "54000.0"
    assert item["date_vente"] == "2026-09-23T09:30:00"
    assert item["surface"] == "50.26" and item["rooms"] == "3" and item["bedrooms"] == "2"
    assert item["zipcode"] == "13013" and item["city"] == "Marseille"
    assert item["lat"] == "43.3370417" and item["land_surface"] is None
    assert details["Statut"] == "adjugé" and details["Occupation"] == "occupé"
    assert details["Surenchère jusqu'au"] == "2026-10-05T00:00:00"
    assert details["Charges annuelles"] == 1033.92
    assert len(details["Documents"]) == 3
    assert details["Clé enchère"] == "?|2026-09-23|marseille|48500"


def test_adjuge_sous_mise_a_prix_et_carence() -> None:
    item = parse("detail_adjuge.html", "logement-de-type-1-a-bordeaux")
    assert item["prix_adjuge"] == "20500.0" and item["type_hint"] == "appartement"
    assert json.loads(item["details_json"])["Surenchère déposée"] == "Oui"
    deserte = parse("detail_deserte.html", "ensemble-immobilier-a-lasalle-gard")
    details = json.loads(deserte["details_json"])
    assert details["Statut"] == "carence" and deserte["prix_adjuge"] is None
    assert details["Frais préalables"] == 7612.59
    assert "faculté de baisse" in details["Faculté de baisse"]


def test_vente_sans_resultat() -> None:
    item = parse("detail_avenir.html", "appartement-a-dijon")
    assert json.loads(item["details_json"])["Statut"] == "à venir"
    assert item["mode_vente"] == "enchere_judiciaire" and item["zipcode"] == "21000"
