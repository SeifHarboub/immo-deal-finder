from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from immo.connectors import bienici, notaires
from immo.connectors.notaires import NotairesConnector, map_annonce
from immo.schema import Criteria


FIXTURES = Path(__file__).parent / "fixtures" / "notaires"
SQL = Path(__file__).parents[1] / "src" / "immo" / "normalize" / "stg_notaires.sql"


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("IMMO_DB", str(tmp_path / "immo.duckdb"))
    monkeypatch.setenv("DVF_DB", str(tmp_path / "absent-dvf.duckdb"))
    monkeypatch.setenv("RENTS_DB", str(tmp_path / "absent-rents.duckdb"))
    return tmp_path


def _page() -> dict:
    return json.loads((FIXTURES / "annonces.json").read_text(encoding="utf-8"))


def _by(kind: str, adjudication: str | None = None) -> dict:
    return next(item for item in _page()["annonceResumeDto"] if item["typeTransaction"] == kind
                and (adjudication is None or item.get("typeAdjudication") == adjudication))


def test_map_classic_sale_uses_total_price() -> None:
    item = map_annonce(_page()["annonceResumeDto"][0])
    assert item["id"] == "2027195" and item["price"] == 22400
    assert item["mode_vente"] == "gre_a_gre" and item["date_vente"] is None
    assert item["zipcode"] == "61500" and item["city"] == "Chailloué"
    assert item["type_hint"] == "MAI" and item["surface"] == 60.0 and item["land_surface"] == 710
    details = json.loads(item["details_json"])
    assert details["Prix hors émoluments"] == 20000 and details["Émoluments de négociation"] == 2400
    assert "Viager" not in details
    assert item["url"].endswith("/2027195")


def test_map_auctions() -> None:
    judicial = map_annonce(_by("VAE", "JUDICIAIRE"))
    assert judicial["mode_vente"] == "enchere_judiciaire"
    source = _by("VAE", "JUDICIAIRE")
    assert judicial["date_vente"] == (source.get("dateFinEncheres") or source["seanceDate"])
    details = json.loads(judicial["details_json"])
    assert details["Mise à prix"] == judicial["price"]
    voluntary = map_annonce(_by("VAE", "VOLONTAIRE"))
    assert voluntary["mode_vente"] == "enchere_notariale"
    interactive = map_annonce(_by("VNI"))
    assert interactive["mode_vente"] == "vente_interactive"
    assert interactive["date_vente"] == _by("VNI")["dateFinEncheres"]
    assert json.loads(interactive["details_json"])["Première offre possible"] == 325000


def test_viager_and_detail_enrichment() -> None:
    summary = dict(_page()["annonceResumeDto"][0], viager="OUI")
    detail = json.loads((FIXTURES / "detail.json").read_text(encoding="utf-8"))
    item = map_annonce(summary, detail)
    details = json.loads(item["details_json"])
    assert details["Viager"] == "oui"
    assert details["Taxe foncière"] == 125
    assert len(json.loads(item["images_json"])) > 10
    assert item["lat"] is None and item["lng"] is None
    assert "Chailloué" in item["name"]


def test_incremental_stops_on_old_dates(monkeypatch, temp_db) -> None:
    now = datetime.now(timezone.utc)
    bienici.write_state("notaires", "full_scan", now)
    bienici.write_state("notaires", "watermark", now - timedelta(hours=6))
    page = _page()
    items = page["annonceResumeDto"][:4]
    for index, item in enumerate(items):
        item["dateMaj"] = (now - timedelta(hours=2 + index * 3)).isoformat()
    requested = []

    def fake_get(self, url, params=None, allow_status=frozenset()):
        requested.append(params["page"])
        return {**page, "nbPages": 50, "annonceResumeDto": items}

    monkeypatch.setattr(bienici.PoliteClient, "get_json", fake_get)
    ids = [item["id"] for item in NotairesConnector().fetch(Criteria())]
    # 2 h et 5 h sont récents ; 8 h dépasse le filigrane (6 h + 30 min de marge).
    assert len(ids) == 2 and requested == [1]


def test_crawl_delay_floor(monkeypatch, temp_db) -> None:
    monkeypatch.setenv("SCRAPE_MIN_DELAY_NOTAIRES", "1")
    seen = {}

    def fake_get(self, url, params=None, allow_status=frozenset()):
        seen["delay"] = self.min_delay
        return None

    monkeypatch.setattr(bienici.PoliteClient, "get_json", fake_get)
    list(NotairesConnector().fetch(Criteria(max_pages=1)))
    assert seen["delay"] >= 10


def test_stg_sql(temp_db) -> None:
    from immo.runner import COMMON_RAW_COLUMNS, _write_bronze
    from immo.warehouse import connect

    records = [map_annonce(item) for item in _page()["annonceResumeDto"]]
    assert _write_bronze("notaires", records) == len(records)
    with connect() as con:
        for column, kind in COMMON_RAW_COLUMNS.items():
            con.execute(f'ALTER TABLE raw_notaires ADD COLUMN IF NOT EXISTS "{column}" {kind}')
        con.execute(SQL.read_text(encoding="utf-8"))
        rows = {row[0]: row[1:] for row in con.execute("""
            SELECT external_id, type_bien, mode_vente, date_vente, prix, categorie
            FROM annonces_stg WHERE source='notaires'
        """).fetchall()}
    assert rows["2027195"][:2] == ("maison", "gre_a_gre")
    judicial = str(_by("VAE", "JUDICIAIRE")["annonceId"])
    assert rows[judicial][1] == "enchere_judiciaire" and rows[judicial][2] is not None
    assert {row[4] for row in rows.values()} == {"vente"}
    assert all(isinstance(row[3], float) for row in rows.values())
