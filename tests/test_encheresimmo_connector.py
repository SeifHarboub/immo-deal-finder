import gzip
import json
from pathlib import Path

from immo.connectors.encheresimmo import EncheresImmoConnector


FIXTURES = Path(__file__).parent / "fixtures" / "encheresimmo"


def fixture(name: str) -> str:
    return gzip.decompress((FIXTURES / f"{name}.gz").read_bytes()).decode("utf-8")


def parse(name: str, number: str) -> list[dict]:
    return list(EncheresImmoConnector().parse_page(
        fixture(name), f"https://encheresimmobilieres.fr/ventes/{number}-bien"))


def test_filtre_du_sitemap() -> None:
    connector = EncheresImmoConnector()
    connector._get = lambda url, timeout=None: (fixture("sitemap.xml"), url)
    urls = list(connector._sitemap_urls(connector.sitemap_url))
    assert len(urls) == 5 and all("/ventes/" in url for url in urls)


def test_vente_a_venir_objet_next() -> None:
    [item] = parse("detail_avenir.html", "9587")
    details = json.loads(item["details_json"])
    assert item["price"] == "20000.0" and item["date_vente"] == "2026-11-19T09:00:00"
    assert item["zipcode"] == "06300" and item["lat"] == "43.69896" and item["surface"] == "57.36"
    assert item["rooms"] == "2" and item["image_count"] == 6
    assert details["Tribunal"] == "Tribunal Judiciaire de Nice" and details["Occupation"] == "occupé"
    assert details["Statut"] == "à venir" and details["Clé enchère"] == "nice|2026-11-19|nice|20000"


def test_resultats_et_archives() -> None:
    [resultat] = parse("resultat_adjuge.html", "9190")
    assert resultat["prix_adjuge"] == "103000.0" and resultat["price"] == "23000.0"
    assert resultat["type_hint"] == "maison"
    [archive] = parse("archive_adjuge.html", "9016")
    details = json.loads(archive["details_json"])
    assert archive["prix_adjuge"] == "107000.0" and archive["price"] == "50000.0"
    assert archive["date_vente"] == "2026-06-18T09:00:00" and archive["zipcode"] == "06130"
    assert details["Statut"] == "adjugé" and details["Occupation"] == "libre"
    [inconnu] = parse("archive_non_communique.html", "8073")
    assert json.loads(inconnu["details_json"])["Statut"] == "non communiqué"


def test_lots_multiples() -> None:
    items = parse("detail_multilot.html", "9593")
    assert [item["id"] for item in items] == ["9593-1", "9593-2"]
    assert [item["price"] for item in items] == ["85000.0", "110000.0"]
    assert [item["surface"] for item in items] == ["13.95", "38.99"]
