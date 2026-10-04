import json
from pathlib import Path

from immo.connectors.bureauxlocaux import BureauxLocauxConnector
from immo.finance import extract


FIXTURES = Path(__file__).parent / "fixtures" / "bureauxlocaux"
URL = "https://www.bureauxlocaux.com/annonce/roubaix-murs--42462834"


def _parse(name: str) -> dict:
    return next(BureauxLocauxConnector().parse_page((FIXTURES / name).read_text(encoding="utf-8"), URL + "?x=1"))


def test_vente_murs_loues() -> None:
    item = _parse("murs-loues.html")
    assert item["id"] == "42462834"
    assert item["type_hint"] == "vente local_commercial"
    assert item["price"] == 80000 and item["surface"] == 67
    assert item["zipcode"] == "59100" and item["city"] == "Roubaix"
    assert item["url"] == URL
    assert item["reference_annonce"] == "460363B-ZED"
    assert "Loyer annuel de 9600 euros" in item["body"]
    finance = extract(item["name"], item["body"], item["details_json"], item["price"], "local_commercial")
    assert finance.loyer_annuel_declare == 9600
    assert finance.segment == "murs"


def test_fonds_et_bureaux_occupes() -> None:
    fonds = _parse("fonds.html")
    assert fonds["type_hint"] == "vente fonds_commerce"
    assert json.loads(fonds["details_json"])["Licence"] == "licence_4"
    bureaux = _parse("bureaux-occupes.html")
    details = json.loads(bureaux["details_json"])
    assert bureaux["type_hint"] == "vente bureau"
    assert details["Occupé"] == "oui" and details["Taxe foncière"] == 1600


def test_location_commerce_loyer_mensuel() -> None:
    item = _parse("location-commerce.html")
    assert item["type_hint"] == "location local_commercial"
    assert item["price"] == 4166.98
    details = json.loads(item["details_json"])
    assert details["Loyer HT HC €/m²/an"] == 454.58
    assert "Loyer annuel" not in details


def test_filtre_url() -> None:
    keep = BureauxLocauxConnector._keep_url
    assert keep("https://www.bureauxlocaux.com/annonce/local-commercial-a-louer--124")
    assert keep("https://www.bureauxlocaux.com/annonce/vente-bureaux-lyon--125")
    assert not keep("https://www.bureauxlocaux.com/annonce/bureaux-a-louer-paris--123")
    assert not keep("https://www.bureauxlocaux.com/listings/123/similar_listings")


def test_normalisation(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IMMO_DB", str(tmp_path / "t.duckdb"))
    monkeypatch.setenv("DVF_DB", str(tmp_path / "absent-dvf.duckdb"))
    monkeypatch.setenv("RENTS_DB", str(tmp_path / "absent-rents.duckdb"))
    from immo import runner
    from immo.warehouse import connect
    runner._write_bronze("bureauxlocaux", [_parse("murs-loues.html"), _parse("location-commerce.html")])
    runner.normalize("bureauxlocaux")
    runner.normalize("bureauxlocaux")  # relance sans doublon
    with connect() as con:
        rows = con.execute("""
            SELECT external_id, categorie, type_bien, prix, loyer, code_postal
            FROM annonces_stg WHERE source='bureauxlocaux' ORDER BY external_id
        """).fetchall()
    assert rows == [
        ("42439959", "location", "local_commercial", None, 4166.98, "75011"),
        ("42462834", "vente", "local_commercial", 80000.0, None, "59100"),
    ]
