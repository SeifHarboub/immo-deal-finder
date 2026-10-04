import gzip
import json
from pathlib import Path

from immo.connectors.agorastore import AgorastoreConnector


FIXTURES = Path(__file__).parent / "fixtures" / "agorastore"


def parse(name: str) -> dict:
    html = gzip.decompress((FIXTURES / f"{name}.gz").read_bytes()).decode("utf-8")
    [item] = AgorastoreConnector().parse_page(html, "https://www.agorastore-immo.fr/vente-occasion/immobilier/x.aspx")
    return item


def test_vente_close_sans_enchere() -> None:
    item = parse("detail_carence.html")
    details = json.loads(item["details_json"])
    assert item["id"] == "382043" and item["price"] == "240000.0" and item["prix_adjuge"] is None
    assert item["mode_vente"] == "cession_publique" and item["type_hint"] == "maison"
    assert item["surface"] == "357.0" and item["zipcode"] == "30310" and item["city"] == "Vergèze"
    assert item["date_vente"] == "2025-10-23T16:00:00" and item["seller_name"] == "Ville de VERGEZE"
    assert details["Statut"] == "carence" and details["Frais acheteur (%)"] == 9.0
    assert details["Documents"][0]["url"].endswith(".pdf")


def test_vente_a_venir() -> None:
    item = parse("detail_avenir.html")
    details = json.loads(item["details_json"])
    assert details["Statut"] == "à venir" and item["lat"] == "48.3712349"
    assert details["Début des enchères"] == "2026-10-13T14:00:00" and item["image_count"] > 0
