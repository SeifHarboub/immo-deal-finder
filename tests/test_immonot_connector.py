import json
from pathlib import Path

import pytest

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.immonot import ImmonotConnector, _wanted


FIXTURES = Path(__file__).parent / "fixtures" / "immonot"
SQL = Path(__file__).parents[1] / "src" / "immo" / "normalize" / "stg_immonot.sql"
BASE = "https://www.immonot.com/immobilier-notaire/detail/"
PAGES = {
    "maison": BASE + "0000011779__w17903482537067320/achat-maison-a-vendre-35140-gosne-ille-et-vilaine.html",
    "achat-appartement": BASE + "0000011745_296_61585104/achat-appartement-a-vendre-35400-saint-malo-ille-et-vilaine.html",
    "encheres": BASE + "0000010986__w17906650986664415/encheres-classiques-maison-23150-ahun-creuse.html",
    "viager": BASE + "0000013356_63022-82/viager-fonds-et-ou-murs-commerciaux-63000-clermont-ferrand-puy-de-dome.html",
    "location-fonds": BASE + "0000013104__w17900009013969389/location-fonds-et-ou-murs-commerciaux-59270-bailleul-nord.html",
    "achat-terrain-a-batir": BASE + "0000012859_56079-1654/achat-terrain-a-batir-a-vendre-56590-groix-morbihan.html",
}


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("IMMO_DB", str(tmp_path / "immo.duckdb"))
    monkeypatch.setenv("DVF_DB", str(tmp_path / "absent-dvf.duckdb"))
    monkeypatch.setenv("RENTS_DB", str(tmp_path / "absent-rents.duckdb"))
    return tmp_path


def _parse(name: str) -> dict:
    html = (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
    items = list(ImmonotConnector().parse_page(html, PAGES[name]))
    assert len(items) == 1
    return items[0]


def test_url_filter() -> None:
    assert _wanted(PAGES["maison"]) and _wanted(PAGES["viager"]) and _wanted(PAGES["encheres"])
    assert _wanted(PAGES["location-fonds"])
    assert not _wanted(BASE + "l800243290/location-maison-35300-fougeres.html")
    assert not _wanted("https://www.immonot.com/immobilier-notaire/annonces/D35/achat-maison-a-vendre.html")


def test_house_with_list_typed_product() -> None:
    item = _parse("maison")
    assert item["id"] == "0000011779__w17903482537067320"
    assert item["price"] == 290510 and item["surface"] == 97 and item["land_surface"] == 473
    assert item["rooms"] == 5 and item["bedrooms"] == 3
    assert item["zipcode"] == "35140" and item["city"] == "Gosné"
    assert item["type_hint"] == "maison" and item["reference_annonce"] == "137/4149"
    assert item["dpe"] == "B" and item["ges"] == "B"
    assert float(item["lat"]) == 48.2492 and float(item["lng"]) == -1.4606
    assert item["seller_name"].startswith("SCP Claudine")
    assert all("cdn.notariat.services/immonot/photo/" in url for url in json.loads(item["images_json"]))
    assert "\n" in item["body"]
    details = json.loads(item["details_json"])
    assert details["Précision cartographique"] == "approximative"
    assert details["Prix hors honoraires"] == 278000 and details["Honoraires de négociation"] == 12510
    assert details["DPE consommation"] == 56 and details["Coût énergétique annuel max"] == 852


def test_apartment_condo_charges_and_text_dpe() -> None:
    item = _parse("achat-appartement")
    details = json.loads(item["details_json"])
    assert details["Charges annuelles de copropriété"] == 120
    assert details["Nbre de lots de la copropriété"] == "3"
    assert item["dpe"] == "D"


def test_special_sales() -> None:
    auction = _parse("encheres")
    assert auction["price"] is None and auction["dpe"] == "D"
    viager = _parse("viager")
    details = json.loads(viager["details_json"])
    assert details["Viager"] == "oui" and details["Bouquet"] == 30000
    assert details["Rente mensuelle viagère"] == 611
    rental = _parse("location-fonds")
    assert rental["price"] == 450 and rental["type_hint"] == "local commercial"
    land = _parse("achat-terrain-a-batir")
    assert land["type_hint"] == "terrain" and land["land_surface"] == 458 and land["surface"] is None


def test_energy_fallback_on_tallest_bar() -> None:
    from immo.connectors.immonot import _energy
    html = (FIXTURES / "maison.html").read_text(encoding="utf-8")
    stripped = html.replace("(DPE) - classe B", "(DPE)")
    assert _energy(stripped, "dpe") == "B"


def test_sitemap_reads_only_detail_chunks(monkeypatch) -> None:
    connector = ImmonotConnector()

    class Response:
        text = """<sitemapindex>
          <sitemap><loc>https://www.immonot.com/sitemap/landing/chunk-1.xml</loc></sitemap>
          <sitemap><loc>https://www.immonot.com/sitemap/annonce_detail_ancien/chunk-1.xml</loc></sitemap>
          <sitemap><loc>https://www.immonot.com/sitemap/annonce_detail_ancien/chunk-2.xml</loc></sitemap>
        </sitemapindex>"""

        def raise_for_status(self) -> None:
            pass

    monkeypatch.setattr(connector.session, "get", lambda url, timeout=30: Response())
    fetched = []

    def fake_chunk(self, url, seen=None):
        fetched.append(url)
        yield from (f"{url}#{index}" for index in range(2))

    monkeypatch.setattr(AgencyJsonLdConnector, "_sitemap_urls", fake_chunk)
    urls = list(connector._sitemap_urls(connector.sitemap_url))
    assert all("annonce_detail_ancien" in url for url in fetched) and len(fetched) == 2
    assert urls[0].endswith("chunk-1.xml#0") and urls[1].endswith("chunk-2.xml#0")


def test_stg_sql_accepts_old_and_new_rows(temp_db) -> None:
    from immo.runner import COMMON_RAW_COLUMNS, _write_bronze
    from immo.warehouse import connect

    legacy = {
        "id": "legacy-1", "name": "Maison", "price": "150000", "surface": "80",
        "land_surface": None, "rooms": "4", "zipcode": "23000", "city": "Guéret",
        "lat": None, "lng": None, "type_hint": "House",
        "url": "https://www.immonot.com/annonce-immobiliere/legacy-1/achat-maison-a-vendre.html",
        "body": "Ancienne forme", "raw_payload": "{}",
    }
    assert _write_bronze("immonot", [legacy]) == 1
    records = [_parse(name) for name in PAGES]
    assert _write_bronze("immonot", records) == len(records)
    with connect() as con:
        for column, kind in COMMON_RAW_COLUMNS.items():
            con.execute(f'ALTER TABLE raw_immonot ADD COLUMN IF NOT EXISTS "{column}" {kind}')
        con.execute(SQL.read_text(encoding="utf-8"))
        rows = {row[0]: row[1:] for row in con.execute("""
            SELECT external_id, type_bien, categorie, prix, loyer, mode_vente, surface_terrain
            FROM annonces_stg WHERE source='immonot'
        """).fetchall()}
    assert rows["legacy-1"][:3] == ("maison", "vente", 150000.0)
    assert rows["0000011779__w17903482537067320"][:3] == ("maison", "vente", 290510.0)
    assert rows["0000013104__w17900009013969389"][:4] == ("local_commercial", "location", None, 450.0)
    assert rows["0000010986__w17906650986664415"][4] == "enchere_notariale"
    assert rows["0000012859_56079-1654"][0] == "terrain" and rows["0000012859_56079-1654"][5] == 458
