import gzip
import json
from pathlib import Path

from immo.connectors.heures36 import Heures36Connector


FIXTURE = Path(__file__).parent / "fixtures" / "heures36" / "detail_avenir.html.gz"
URL = "https://www.36heures.immo/fr/annonce/01M41E3PQYTB338S8Y88PJ7EDT/maison-sceautres-07400-6p-127m2-127200euros.html"


def test_vente_interactive() -> None:
    [item] = Heures36Connector().parse_page(gzip.decompress(FIXTURE.read_bytes()).decode("utf-8"), URL)
    details = json.loads(item["details_json"])
    assert item["id"] == "01M41E3PQYTB338S8Y88PJ7EDT" and item["mode_vente"] == "vente_interactive"
    assert item["price"] == "127200" and item["surface"] == "127.0" and item["land_surface"] == "1231.0"
    assert item["rooms"] == "6" and item["bedrooms"] == "4" and item["type_hint"] == "maison"
    assert item["zipcode"] == "07400" and item["dpe"] == "D"
    assert item["date_vente"] == "2026-12-05T00:00:00"
    assert details["Début des offres"] == "2026-10-05T19:15:00" and details["Statut"] == "à venir"
    assert details["Honoraires"] == "Honoraires à la charge du vendeur"
