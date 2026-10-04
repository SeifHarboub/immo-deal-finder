import json
import os
from pathlib import Path
import tempfile


def test_bronze_survit_a_un_changement_de_type(monkeypatch):
    db = Path(tempfile.gettempdir()) / "immo-bronze-types.duckdb"
    db.unlink(missing_ok=True)
    monkeypatch.setenv("IMMO_DB", str(db))
    monkeypatch.setenv("DVF_DB", str(Path(tempfile.gettempdir()) / "absent-dvf.duckdb"))
    from immo.runner import _write_bronze
    from immo.warehouse import connect
    assert _write_bronze("typetest", [{"id": "a", "price": 165000}]) == 1
    # Une fourchette après un nombre faisait échouer toute la source Bien'ici.
    assert _write_bronze("typetest", [{"id": "b", "price": [165000, 205000]}]) == 1
    with connect() as con:
        rows = con.execute("SELECT id, TRY_CAST(price AS DOUBLE) FROM raw_typetest ORDER BY id").fetchall()
    assert rows == [("a", 165000.0), ("b", None)]
    db.unlink(missing_ok=True)


def test_bienici_fourchette_de_prix():
    from immo.connectors.bienici import map_ad
    record = map_ad({"id": "x", "price": [165000, 205000], "surfaceArea": [40, 80], "title": "Lots"})
    assert record["price"] is None and record["surface"] is None
    details = json.loads(record["details_json"])
    assert details["Fourchette de prix"] == "165000 – 205000"
    assert details["Lots multiples"] == "oui"
