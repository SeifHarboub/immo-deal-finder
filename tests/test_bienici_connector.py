from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from immo.connectors import bienici
from immo.connectors.bienici import BienIciConnector, map_ad
from immo.schema import Criteria


FIXTURES = Path(__file__).parent / "fixtures" / "bienici"
SQL = Path(__file__).parents[1] / "src" / "immo" / "normalize" / "stg_bienici.sql"


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("IMMO_DB", str(tmp_path / "immo.duckdb"))
    monkeypatch.setenv("DVF_DB", str(tmp_path / "absent-dvf.duckdb"))
    monkeypatch.setenv("RENTS_DB", str(tmp_path / "absent-rents.duckdb"))
    return tmp_path


def _ads() -> list[dict]:
    return json.loads((FIXTURES / "search.json").read_text(encoding="utf-8"))["realEstateAds"]


def test_map_ad_extracts_core_fields() -> None:
    item = map_ad(_ads()[0])
    assert item["id"] == "hektor-1223_EXPERTIMO22-268804"
    assert item["price"] == 483600 and item["surface"] == 105
    assert item["rooms"] == 4 and item["bedrooms"] == 2
    assert item["zipcode"] == "33000" and item["city"] == "Bordeaux"
    assert item["type_hint"] == "flat/vente"
    assert item["url"] == "https://www.bienici.com/annonce/hektor-1223_EXPERTIMO22-268804"
    assert "<br>" not in item["body"] and "\n" in item["body"]
    assert item["image_count"] == len(json.loads(item["images_json"])) > 0
    assert item["published_at"].startswith("2026-10-04")
    details = json.loads(item["details_json"])
    # Disque de 750 m : position floutée.
    assert details["Précision cartographique"] == "approximative"
    assert details["Année de construction"] == 1975
    assert json.loads(item["raw_payload"])["id"] == item["id"]


def test_map_ad_hides_epoch_dates_and_maps_shop() -> None:
    shop = map_ad(_ads()[2])
    assert shop["type_hint"] == "shop/vente fond de commerce"
    ad = dict(_ads()[0], publicationDate="1970-01-01T00:00:00.000Z",
              blurInfo={"type": "exact", "position": {"lat": 45.0, "lon": 5.0}})
    item = map_ad(ad)
    assert item["published_at"] is None
    assert item["lat"] == 45.0
    assert "Précision cartographique" not in json.loads(item["details_json"])


def test_full_scan_keyset_pagination_on_price(monkeypatch, temp_db) -> None:
    """Au-delà de 2 500 annonces, la recherche repart du dernier prix lu."""
    connector = BienIciConnector()
    monkeypatch.setattr(connector, "_zones", lambda: {"35": ["-7465"]})
    monkeypatch.setenv("BIENICI_MODE", "full")
    stock = [{"id": f"ad-{price}", "adType": "buy", "price": price} for price in range(6000)]
    lows = []

    def fake_search(filters):
        assert filters["sortBy"] == "price" and filters["from"] + filters["size"] <= 2500
        matching = [ad for ad in stock if ad["price"] >= filters["minPrice"]]
        if filters["from"] == 0:
            lows.append(filters["minPrice"])
        return {"total": len(matching),
                "realEstateAds": matching[filters["from"]:filters["from"] + filters["size"]]}

    monkeypatch.setattr(connector, "_search", fake_search)
    items = list(connector.fetch(Criteria()))
    assert lows == [0, 2499, 4998]
    assert len({item["id"] for item in items}) == 6000
    assert connector.pages == 13
    assert bienici.read_state("bienici", "full_scan") is not None


def test_incremental_stops_at_watermark(monkeypatch, temp_db) -> None:
    now = datetime.now(timezone.utc)
    bienici.write_state("bienici", "full_scan", now)
    bienici.write_state("bienici", "watermark", now - timedelta(hours=6))
    connector = BienIciConnector()
    monkeypatch.setattr(connector, "_zones", lambda: {"35": ["-7465"], "56": ["-7447"]})
    monkeypatch.setattr(bienici.PoliteClient, "_wait", lambda self: None)

    def fake_search(filters):
        assert filters["sortBy"] == "modificationDate"
        zone = filters["zoneIdsByTypes"]["zoneIds"][0]
        ads = [{"id": f"{zone}-{hours}", "adType": "buy",
                "modificationDate": (now - timedelta(hours=hours)).isoformat()} for hours in (1, 3, 12, 20)]
        return {"total": 4, "realEstateAds": ads[filters["from"]:filters["from"] + filters["size"]]}

    monkeypatch.setattr(connector, "_search", fake_search)
    ids = [item["id"] for item in connector.fetch(Criteria())]
    assert ids == ["-7465-1", "-7465-3", "-7447-1", "-7447-3"]


def test_stg_sql_normalizes_types(temp_db) -> None:
    from immo.runner import COMMON_RAW_COLUMNS, _write_bronze
    from immo.warehouse import connect

    records = [map_ad(ad) for ad in _ads()]
    records.append(map_ad(dict(_ads()[0], id="b1", propertyType="building")))
    records.append(map_ad(dict(_ads()[0], id="p1", propertyType="parking")))
    assert _write_bronze("bienici", records) == len(records)
    with connect() as con:
        for column, kind in COMMON_RAW_COLUMNS.items():
            con.execute(f'ALTER TABLE raw_bienici ADD COLUMN IF NOT EXISTS "{column}" {kind}')
        con.execute(SQL.read_text(encoding="utf-8"))
        rows = dict(con.execute(
            "SELECT external_id, type_bien FROM annonces_stg WHERE source='bienici'"
        ).fetchall())
        flat = con.execute("""
            SELECT prix, surface_bati, code_postal, lat, mode_vente, seller_type, published_at
            FROM annonces_stg WHERE external_id='hektor-1223_EXPERTIMO22-268804'
        """).fetchone()
    assert rows["hektor-1223_EXPERTIMO22-268804"] == "appartement"
    assert rows[_ads()[2]["id"]] == "fonds_commerce"
    assert rows[_ads()[3]["id"]] == "local_commercial"
    assert rows["b1"] == "immeuble" and rows["p1"] == "autre"
    assert flat[0] == 483600 and flat[1] == 105 and flat[2] == "33000"
    assert flat[3] is not None and flat[4] == "gre_a_gre" and flat[5] == "pro" and flat[6] is not None
