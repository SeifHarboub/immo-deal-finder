import gzip
import json
from pathlib import Path

import pytest

from immo.connectors.licitor import (
    LicitorConnector, _Robots, cle_enchere, occupation, parse_amount, parse_fr_date,
    postcode_from_city, type_from_text,
)
from immo.schema import Criteria


FIXTURES = Path(__file__).parent / "fixtures" / "licitor"
URL = "https://www.licitor.com/annonce/11/01/37/vente-aux-encheres/un-appartement/paris-6eme/paris/110137.html"


def fixture(name: str) -> str:
    return gzip.decompress((FIXTURES / f"{name}.gz").read_bytes()).decode("utf-8")


def parse(name: str, url: str = URL) -> list[dict]:
    return list(LicitorConnector().parse_page(fixture(name), url))


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("IMMO_DB", str(tmp_path / "immo.duckdb"))
    monkeypatch.setenv("DVF_DB", str(tmp_path / "absent-dvf.duckdb"))
    monkeypatch.setenv("RENTS_DB", str(tmp_path / "absent-rents.duckdb"))
    monkeypatch.setenv("ENCHERES_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


def test_utilitaires() -> None:
    assert parse_amount("48 500,00 €") == 48500
    assert parse_amount("20.000 €") == 20000
    assert parse_amount("11 587.00 €") == 11587
    assert parse_amount("1.315,17 m²") == 1315.17
    assert parse_fr_date("jeudi 1er octobre 2026 à 14h30") == "2026-10-01T14:30:00"
    assert parse_fr_date("23/09/2026 à 09:30") == "2026-09-23T09:30:00"
    assert type_from_text("Un bâtiment à usage commercial") == "local_commercial"
    assert type_from_text("Un local à usage d'habitation") == "appartement"
    assert type_from_text("Immeuble à usage de commerce et d’habitation") == "immeuble"
    assert type_from_text("Un emplacement de parking") == "autre"
    assert occupation("Le bien est libre de toute occupation") == "libre"
    assert occupation("Occupé par la propriétaire") == "occupé"
    assert postcode_from_city("Paris 6ème") == "75006"
    assert postcode_from_city("Marseille 13e") == "13013"
    assert cle_enchere("Tribunal Judiciaire de St Etienne", "2025-10-16T14:00:00", "SAINT-ÉTIENNE", 45000) \
        == "saint-etienne|2025-10-16|saint-etienne|45000"


def test_robots_jokers() -> None:
    robots = _Robots("User-agent: *\nDisallow: /data/pub/\nAllow: /data/pub/media/annonce/\nDisallow: /*.asp$\n")
    assert robots.allowed("https://www.licitor.com/annonce/1/2/3/x.html")
    assert not robots.allowed("https://www.licitor.com/data/pub/x.pdf")
    assert robots.allowed("https://www.licitor.com/data/pub/media/annonce/1.pdf")
    assert not robots.allowed("https://www.licitor.com/contact.asp")


def test_vente_a_venir() -> None:
    [item] = parse("detail_avenir.html")
    details = json.loads(item["details_json"])
    assert item["id"] == "110137" and item["price"] == "400000.0" and item["prix_adjuge"] is None
    assert item["date_vente"] == "2026-11-05T14:00:00"
    assert item["surface"] == "67.52" and item["type_hint"] == "appartement"
    assert item["zipcode"] == "75006" and item["lat"] == "48.8486404"
    assert item["mode_vente"] == "enchere_judiciaire"
    assert details["Statut"] == "à venir" and details["Occupation"] == "occupé"
    assert details["Tribunal"].startswith("Tribunal Judiciaire de Paris")
    assert details["Visites"].startswith("Visite sur place")
    assert details["Clé enchère"] == "paris|2026-11-05|paris|400000"


def test_resultats_passes() -> None:
    [adjuge] = parse("detail_adjuge.html")
    assert adjuge["price"] == "50000.0" and adjuge["prix_adjuge"] == "510000.0"
    assert json.loads(adjuge["details_json"])["Statut"] == "adjugé"
    assert len(json.loads(adjuge["details_json"])["Documents"]) == 2
    [carence] = parse("detail_carence.html")
    assert json.loads(carence["details_json"])["Statut"] == "carence" and carence["price"] == "10000.0"
    assert carence["type_hint"] == "autre"
    [retire] = parse("detail_non_requise.html")
    assert json.loads(retire["details_json"])["Statut"] == "retiré"


def test_annonce_en_plusieurs_lots() -> None:
    items = parse("detail_multilot.html")
    assert [item["id"] for item in items] == ["110146-1", "110146-2"]
    assert [item["price"] for item in items] == ["1850000.0", "150000.0"]
    assert json.loads(items[1]["details_json"])["Occupation"] == "libre"


def test_collecte_hors_ligne_et_curseur(isolated_db, monkeypatch) -> None:
    from immo import runner
    from immo.warehouse import connect

    connector = LicitorConnector()
    listing = {"prochaines-ventes": fixture("prochaines.html"),
               "historique-des-adjudications": fixture("historique.html")}
    details = [fixture(name) for name in ("detail_avenir.html", "detail_adjuge.html", "detail_carence.html")]
    calls: list[str] = []

    def fake_get(url, timeout=None):
        calls.append(url)
        for key, html in listing.items():
            if key in url:
                return html, url
        return details[len([c for c in calls if "/annonce/" in c]) % 3], url

    monkeypatch.setattr(connector, "_get", fake_get)
    monkeypatch.setattr(connector, "REGIONS", ("paris-et-ile-de-france",))
    monkeypatch.setenv("LICITOR_HISTORY_PAGES_PER_RUN", "1")
    monkeypatch.setenv("LICITOR_FRESH_MAX_PAGES", "1")
    monkeypatch.setenv("LICITOR_UPCOMING_MAX_PAGES", "1")
    records = list(connector.fetch(Criteria(max_pages=20)))
    assert records and all("mode_vente" in record for record in records)
    state = json.loads((isolated_db / "state" / "licitor.json").read_text())
    assert state["history_next_page"]["paris-et-ile-de-france"] == 2

    runner._write_bronze("licitor", records)
    runner.normalize("licitor")
    with connect() as con:
        rows = con.execute("""
            SELECT external_id, mode_vente, prix, prix_adjuge, date_vente, active, categorie
            FROM annonces_stg WHERE source='licitor' ORDER BY external_id
        """).fetchall()
    by_id = {row[0]: row for row in rows}
    assert by_id["109451"][3] == 510000 and by_id["109451"][5] is False
    assert by_id["110137"][1] == "enchere_judiciaire"
    assert all(row[6] == "vente" for row in rows)
