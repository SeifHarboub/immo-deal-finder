from collections.abc import Iterable
from datetime import datetime, timezone
from collections import Counter
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from immo.connectors import discover_connectors, get_connector
from immo.schema import Criteria
from immo.warehouse import connect


SQL_ROOT = Path(__file__).parent

COMMON_RAW_COLUMNS = {
    "id": "VARCHAR", "name": "VARCHAR", "price": "VARCHAR",
    "surface": "VARCHAR", "land_surface": "VARCHAR", "rooms": "VARCHAR",
    "bedrooms": "VARCHAR", "zipcode": "VARCHAR", "city": "VARCHAR",
    "lat": "VARCHAR", "lng": "VARCHAR", "type_hint": "VARCHAR",
    "url": "VARCHAR", "body": "VARCHAR", "seller_name": "VARCHAR",
    "dpe": "VARCHAR", "ges": "VARCHAR", "image_count": "BIGINT",
    "published_at": "VARCHAR", "details_json": "VARCHAR",
    "images_json": "VARCHAR", "reference_annonce": "VARCHAR",
    "raw_payload": "VARCHAR", "_collected_at": "VARCHAR",
}

LEBONCOIN_RAW_COLUMNS = {
    "index_date": "VARCHAR", "expiration_date": "VARCHAR", "body": "VARCHAR",
    "category_id": "VARCHAR", "status": "VARCHAR", "attr_bedrooms": "VARCHAR",
    "attr_energy_rate": "VARCHAR", "attr_ges": "VARCHAR", "attr_furnished": "VARCHAR",
    "attr_elevator": "VARCHAR", "attr_pool": "VARCHAR", "attr_parking": "VARCHAR",
    "seller_type": "VARCHAR", "seller_name": "VARCHAR", "image_count": "BIGINT",
    "attributes_json": "VARCHAR", "images_json": "VARCHAR", "owner_json": "VARCHAR",
    "raw_payload": "VARCHAR", "_collected_at": "VARCHAR",
}


def _safe_source(source: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", source):
        raise ValueError(f"Nom de source invalide : {source}")
    return source


def _write_bronze(
    source: str, records: Iterable[dict], run_id: str | None = None,
) -> int:
    source = _safe_source(source)
    batch_size = max(100, int(os.getenv("BRONZE_BATCH_SIZE", "1000")))
    count = 0
    batch: list[dict] = []
    seen: set[str] = set()

    def flush(rows: list[dict]) -> None:
        nonlocal count
        temp_path: str | None = None
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", encoding="utf-8", delete=False) as stream:
            temp_path = stream.name
            for row in rows:
                payload = json.dumps(row, ensure_ascii=False, default=str)
                # A few agency descriptions contain lone UTF-16 surrogates.
                # They are not valid UTF-8 and used to abort an entire source
                # after thousands of successful downloads. Replace only those
                # invalid code points while preserving the rest of the record.
                payload = payload.encode("utf-8", errors="replace").decode("utf-8")
                stream.write(payload + "\n")
        try:
            escaped_path = temp_path.replace("'", "''")
            with connect() as con:
                con.execute(f"CREATE TEMP TABLE incoming_batch AS SELECT * FROM read_json_auto('{escaped_path}')")
                exists = con.execute(
                    "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [f"raw_{source}"]
                ).fetchone()[0]
                if not exists:
                    con.execute(f"CREATE TABLE raw_{source} AS SELECT * FROM incoming_batch LIMIT 0")
                else:
                    existing_columns = {row[0]: row[1] for row in con.execute(f"DESCRIBE raw_{source}").fetchall()}
                    for name, column_type, *_ in con.execute("DESCRIBE incoming_batch").fetchall():
                        if name not in existing_columns:
                            identifier = name.replace('"', '""')
                            con.execute(f'ALTER TABLE raw_{source} ADD COLUMN "{identifier}" {column_type}')
                        elif existing_columns[name] == "JSON" and column_type != "JSON":
                            identifier = name.replace('"', '""')
                            con.execute(
                                f'ALTER TABLE raw_{source} ALTER COLUMN "{identifier}" '
                                f'TYPE VARCHAR USING CAST("{identifier}" AS VARCHAR)'
                            )
                con.execute(f"INSERT INTO raw_{source} BY NAME SELECT * FROM incoming_batch")
                if run_id:
                    con.execute(
                        "UPDATE collection_runs SET rows_collected=? WHERE run_id=?",
                        [count + len(rows), run_id],
                    )
            count += len(rows)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    collected_at = datetime.now(timezone.utc).isoformat()
    for record in records:
        identity = str(record.get("id") or record.get("url") or "")
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        batch.append({**record, "_collected_at": collected_at})
        if len(batch) >= batch_size:
            flush(batch)
            batch = []
    if batch:
        flush(batch)
    return count


def collect(source: str, c: Criteria) -> int:
    discover_connectors()
    run_id = str(uuid.uuid4())
    with connect() as con:
        con.execute(
            "INSERT INTO collection_runs VALUES (?, ?, ?, now(), NULL, 'running', 0, NULL)",
            [run_id, source, c.search_url],
        )
    try:
        count = _write_bronze(source, get_connector(source).fetch(c), run_id=run_id)
        with connect() as con:
            con.execute(
                "UPDATE collection_runs SET finished_at=now(), status='success', rows_collected=? WHERE run_id=?",
                [count, run_id],
            )
        return count
    except Exception as exc:
        with connect() as con:
            con.execute(
                "UPDATE collection_runs SET finished_at=now(), status='failed', error=? WHERE run_id=?",
                [str(exc)[:2000], run_id],
            )
        raise


def normalize(source: str) -> None:
    source = _safe_source(source)
    path = SQL_ROOT / "normalize" / f"stg_{source}.sql"
    if not path.exists():
        raise ValueError(f"Normalisation absente pour {source}")
    with connect() as con:
        table_exists = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name=?",
            [f"raw_{source}"],
        ).fetchone()[0]
        if not table_exists:
            return
        for column, column_type in COMMON_RAW_COLUMNS.items():
            con.execute(f'ALTER TABLE raw_{source} ADD COLUMN IF NOT EXISTS "{column}" {column_type}')
        if source == "leboncoin":
            for column, column_type in LEBONCOIN_RAW_COLUMNS.items():
                con.execute(f"ALTER TABLE raw_leboncoin ADD COLUMN IF NOT EXISTS {column} {column_type}")
        con.execute(path.read_text(encoding="utf-8"))
        if source == "immonot":
            # Older connector versions accidentally ingested category/search
            # pages as listings. They have no stable property identity and can
            # create nonsensical valuations, so remove only that legacy shape.
            con.execute("""
                DELETE FROM annonces_stg
                WHERE source='immonot'
                  AND url NOT LIKE '%/annonce-immobiliere/%'
            """)
            con.execute("""
                DELETE FROM raw_immonot
                WHERE url NOT LIKE '%/annonce-immobiliere/%'
            """)
        # Politique produit globale : aucune location résidentielle ou foncière.
        # Les locations restent admises uniquement pour les fonds et locaux
        # commerciaux, quelle que soit la façon dont une source les nomme.
        con.execute("""
            DELETE FROM annonces_stg
            WHERE categorie = 'location'
              AND type_bien NOT IN ('fonds_commerce', 'local_commercial')
        """)
        # Bronze reste un état brut exploitable, mais une copie par annonce est
        # suffisante. Sans ce compactage, les revisites hebdomadaires feraient
        # croître les scans et les temps de MERGE sans apporter d'information.
        con.execute(f"""
            DELETE FROM raw_{source}
            WHERE rowid IN (
                SELECT rowid FROM raw_{source}
                QUALIFY row_number() OVER (
                    PARTITION BY COALESCE(CAST(id AS VARCHAR), url, CAST(rowid AS VARCHAR))
                    ORDER BY COALESCE(TRY_CAST(_collected_at AS TIMESTAMP), now()) DESC,
                             rowid DESC
                ) > 1
            )
        """)
        from immo.comparables import refresh_comparables
        refresh_comparables(con, source)
        from immo.rent_estimates import refresh_rent_estimates
        refresh_rent_estimates(con, source)


def score() -> None:
    with connect() as con:
        con.execute((SQL_ROOT / "scoring" / "scoring.sql").read_text(encoding="utf-8"))
        # The complete reliability model includes text/JSON risk signals. Doing
        # that work twice on every HTTP request became too slow beyond 100k ads,
        # so it is refreshed once at the end of each successful sync.
        from immo.web.app import DEALS_CTE
        con.execute("CREATE OR REPLACE TABLE deal_analysis AS " + DEALS_CTE + " SELECT * FROM deals")


def deals(limit: int = 20) -> list[tuple]:
    with connect() as con:
        exists = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'affaires'"
        ).fetchone()[0]
        if not exists:
            rows = []
        else:
            rows = con.execute("""
                SELECT niveau_affaire, titre, prix, surface_bati,
                       round(decote * 100, 1), round(prix_a_viser), url
                FROM affaires
                WHERE niveau_affaire IN ('excellente', 'bonne')
                ORDER BY decote ASC LIMIT ?
            """, [limit]).fetchall()
    if not rows:
        print("Aucune affaire.")
        return []
    print("niveau | titre | prix | surface | décote % | prix à viser | url")
    for row in rows:
        print(" | ".join("" if value is None else str(value) for value in row))
    return rows


def stats() -> None:
    with connect() as con:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        raw = con.execute("SELECT count(*) FROM raw_leboncoin").fetchone()[0] if "raw_leboncoin" in tables else 0
        silver = con.execute("SELECT count(*) FROM annonces_stg").fetchone()[0]
        gold = con.execute("SELECT count(*) FROM affaires").fetchone()[0] if "affaires" in tables else 0
        refs = con.execute("SELECT count(*) FROM prix_reference").fetchone()[0]
        runs = con.execute("""
            SELECT status, rows_collected, started_at, search_url
            FROM collection_runs ORDER BY started_at DESC LIMIT 5
        """).fetchall()
    print(f"bronze={raw} | annonces uniques={silver} | références DVF={refs} | scorées={gold}")
    for status, count, started, url in runs:
        print(f"{started} | {status} | {count} | {url or '-'}")


def sector(code_postal: str, type_local: str | None = None, limit: int = 10) -> None:
    with connect() as con:
        refs = con.execute("""
            SELECT type_local, nb_ventes, round(q1_eur_m2), round(median_eur_m2),
                   round(q3_eur_m2), derniere_vente
            FROM prix_reference
            WHERE code_postal=? AND (? IS NULL OR type_local=?) ORDER BY type_local
        """, [code_postal, type_local, type_local]).fetchall()
        sales = con.execute("""
            SELECT date_mutation, type_local, surface_bati, valeur_fonciere,
                   round(prix_m2), adresse, commune
            FROM ventes_dvf WHERE code_postal=? AND (? IS NULL OR type_local=?)
            ORDER BY date_mutation DESC LIMIT ?
        """, [code_postal, type_local, type_local, limit]).fetchall()
    print("Référence : type | ventes | q1 €/m² | médiane €/m² | q3 €/m² | dernière vente")
    for row in refs:
        print(" | ".join(str(value) for value in row))
    print("Dernières ventes : date | type | surface | valeur | €/m² | adresse | commune")
    for row in sales:
        print(" | ".join(str(value) for value in row))


def schema_report(quiet: bool = False) -> dict:
    """Mesure les champs réellement disponibles sans supposer le format du site."""
    with connect() as con:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        if "raw_leboncoin" not in tables:
            report = {"generated_at": datetime.now(timezone.utc).isoformat(), "raw_rows": 0,
                      "message": "Aucune annonce Leboncoin collectée"}
        else:
            columns = [row[0] for row in con.execute("DESCRIBE raw_leboncoin").fetchall()]
            expressions = [f'count("{column.replace(chr(34), chr(34) * 2)}")' for column in columns]
            row = con.execute("SELECT count(*), " + ", ".join(expressions) + " FROM raw_leboncoin").fetchone()
            total = row[0]
            coverage = {
                column: {"non_null": row[index + 1],
                         "percent": round(100 * row[index + 1] / total, 1) if total else 0.0}
                for index, column in enumerate(columns)
            }
            payloads = con.execute("""
                SELECT raw_payload FROM raw_leboncoin
                WHERE raw_payload IS NOT NULL ORDER BY _collected_at DESC LIMIT 1000
            """).fetchall()
            keys: Counter[str] = Counter()
            for (payload,) in payloads:
                try:
                    value = json.loads(payload)
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    keys.update(value.keys())
            report = {
                "generated_at": datetime.now(timezone.utc).isoformat(), "raw_rows": total,
                "raw_column_coverage": coverage,
                "payload_sample_size": len(payloads),
                "payload_top_level_keys": dict(keys.most_common()),
            }
    path = Path("data/schema-report.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    if not quiet:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        print(f"Rapport écrit dans {path}")
    return report
