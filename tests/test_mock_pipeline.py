import os
from pathlib import Path
import tempfile

os.environ["IMMO_DB"] = str(Path(tempfile.gettempdir()) / "immo-deal-finder-test.duckdb")
os.environ["DVF_DB"] = str(Path(tempfile.gettempdir()) / "missing-dvf-pipeline-test.duckdb")

from immo.runner import _write_bronze, deals, normalize, score  # noqa: E402
from immo.search_plan import leboncoin_search_urls, parse_price_buckets  # noqa: E402
from immo.connectors.leboncoin import LeboncoinConnector  # noqa: E402
from immo.warehouse import connect  # noqa: E402


def main() -> None:
    db = Path(os.environ["IMMO_DB"])
    db.unlink(missing_ok=True)
    urls = leboncoin_search_urls(["75", "93"], "0-200000,200001-max")
    assert len(urls) == 4
    assert "locations=d_75" in urls[0]
    assert parse_price_buckets("0-200000,200001-max") == [(0, 200000), (200001, None)]
    mapped = LeboncoinConnector._map_ad({
        "list_id": 42, "subject": "Test", "body": "Description", "price": [199000],
        "attributes": [
            {"key": "square", "value": "55"}, {"key": "rooms", "value": "3"},
            {"key": "energy_rate", "value": "D"},
        ],
        "location": {"zipcode": "93000", "city": "Bobigny", "lat": 48.9, "lng": 2.4},
        "owner": {"type": "pro", "name": "Agence test"},
        "images": {"urls": ["a", "b"]},
    })
    assert mapped["attr_square"] == "55"
    assert mapped["attr_energy_rate"] == "D"
    assert mapped["seller_type"] == "pro"
    assert mapped["image_count"] == 2
    assert '"list_id": 42' in mapped["raw_payload"]
    records = [
        {"list_id": "apt-deal", "subject": "Appartement Bobigny", "price": 180000,
         "attr_square": 60, "attr_rooms": 3, "attr_land": None,
         "attr_real_estate_type": "2", "zipcode": "93000", "city": "Bobigny",
         "lat": 48.91, "lng": 2.44, "url": "https://example.test/apt", "first_publication_date": None},
        {"list_id": "house-deal", "subject": "Maison Bobigny", "price": 250000,
         "attr_square": 90, "attr_rooms": 4, "attr_land": 180,
         "attr_real_estate_type": "1", "zipcode": "93000", "city": "Bobigny",
         "lat": 48.91, "lng": 2.44, "url": "https://example.test/house", "first_publication_date": None},
        {"list_id": "market", "subject": "Appartement au marché", "price": 240000,
         "attr_square": 60, "attr_rooms": 3, "attr_land": None,
         "attr_real_estate_type": "2", "zipcode": "93000", "city": "Bobigny",
         "lat": 48.91, "lng": 2.44, "url": "https://example.test/market", "first_publication_date": None},
    ]
    assert _write_bronze("leboncoin", records) == 3
    with connect() as con:
        # Douze ventes comparables par type : la confiance devient « indicative ».
        con.execute("""
            INSERT INTO ventes_dvf (
                id_mutation, date_mutation, code_departement, code_commune, code_postal,
                commune, type_local, surface_bati, valeur_fonciere, prix_m2,
                nombre_pieces, surface_terrain
            )
            SELECT 'apt-' || range, current_date - INTERVAL 200 DAY, '93', '93008', '93000',
                   'Bobigny', 'Appartement', 58 + range % 5, (58 + range % 5) * 4000, 4000, 3, NULL
            FROM range(12)
            UNION ALL
            SELECT 'house-' || range, current_date - INTERVAL 200 DAY, '93', '93008', '93000',
                   'Bobigny', 'Maison', 88 + range % 5, (88 + range % 5) * 3500, 3500, 4, 180
            FROM range(12)
        """)
    normalize("leboncoin")
    score()
    rows = deals(10)
    by_title = {row[3]: row for row in rows}
    assert by_title["Appartement Bobigny"][0] == "excellente"
    assert by_title["Appartement Bobigny"][1] == "decote"
    assert by_title["Appartement Bobigny"][6] == -25.0
    assert by_title["Maison Bobigny"][0] == "bonne"
    assert by_title["Maison Bobigny"][6] == -20.6
    assert "Appartement au marché" not in by_title
    changed = {**records[0], "price": 174000, "subject": "Appartement Bobigny actualisé"}
    assert _write_bronze("leboncoin", [changed]) == 1
    normalize("leboncoin")
    with connect() as con:
        count, price, title = con.execute("""
            SELECT count(*), max(prix), max(titre) FROM annonces_stg
            WHERE source='leboncoin' AND external_id='apt-deal'
        """).fetchone()
    assert count == 1
    assert price == 174000
    assert title == "Appartement Bobigny actualisé"
    print("Test mock réussi.")
    db.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
