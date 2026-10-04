import os
from pathlib import Path
import tempfile

db = Path(tempfile.gettempdir()) / "immo-deal-finder-web-test.duckdb"
db.unlink(missing_ok=True)
os.environ["IMMO_DB"] = str(db)
os.environ["DVF_DB"] = str(Path(tempfile.gettempdir()) / "missing-dvf-test.duckdb")

from immo.warehouse import connect  # noqa: E402
from immo.comparables import refresh_comparables  # noqa: E402
from immo.web.app import deals, filters  # noqa: E402


def main() -> None:
    with connect() as con:
        con.execute("""
            INSERT INTO annonces_stg (
                source, external_id, categorie, type_bien, prix, surface_bati,
                code_postal, ville, titre, url, collected_at, last_seen_at, active, description
            ) VALUES ('immonot','web-1','vente','appartement',180000,60,
                      '93000','Bobigny','Appartement test','https://example.test',now(),now(),true,
                      repeat('Appartement lumineux proche des transports et des commerces. ', 3))
        """)
        con.execute("""
            INSERT INTO prix_reference VALUES
            ('93000','Appartement',15,3200,4000,4600,'2023-01-01','2025-12-31')
        """)
        con.execute("""
            INSERT INTO ventes_dvf (
                id_mutation,date_mutation,code_departement,
                code_commune,code_postal,commune,type_local,surface_bati,
                valeur_fonciere,prix_m2,nombre_pieces
            )
            SELECT 'test-' || range::VARCHAR, current_date - INTERVAL 200 DAY,
                   '93','93008','93000','Bobigny','Appartement',
                   58 + range % 5, (58 + range % 5) * 4000, 4000, 3
            FROM range(10)
        """)
        refresh_comparables(con)
    result = deals(
        q=None, source=[], type_bien=[], postal_code=None, price_min=None,
        price_max=None, surface_min=None, level=[], scored_only=False,
        strategie=[], segment=[], sort="deal", limit=24, offset=0,
    )
    item = result["items"][0]
    # −25 % en confiance moyenne : bonne affaire ; l'excellence exige −30 % net.
    assert item["niveau_affaire"] == "bonne"
    assert item["strategie"] == "decote" and item["segment"] == "residentiel"
    assert item["plafond_excellente"] == 180000
    assert item["plafond_bonne"] == 204000
    assert result["summary"]["scored"] == 1
    assert filters()["sources"] == ["immonot"]
    print("Test API web réussi.")
    db.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
