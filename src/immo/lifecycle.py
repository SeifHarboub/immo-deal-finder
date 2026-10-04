"""Cycle de vie des annonces : disponibilité réelle et historique des prix.

Une annonce retirée ne disparaît d'aucune plateforme de façon explicite : elle
cesse simplement d'être listée. Les connecteurs par sitemap enregistrent donc
l'inventaire complet des URL publiées à chaque passage ; une annonce absente du
dernier inventaire complet est inactive. Les sources sans inventaire (API,
pages de recherche) sont désactivées après `LISTING_STALE_DAYS` sans
observation.

Chaque changement de prix est historisé pour mesurer les baisses, un des
meilleurs signaux de négociation.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

import duckdb


def ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS listing_inventory (
            source VARCHAR, url VARCHAR, listed_at TIMESTAMP,
            PRIMARY KEY (source, url)
        );
        CREATE TABLE IF NOT EXISTS inventory_runs (
            source VARCHAR, run_at TIMESTAMP, url_count BIGINT, complete BOOLEAN
        );
        CREATE TABLE IF NOT EXISTS prix_historique (
            source VARCHAR, external_id VARCHAR, observed_at TIMESTAMP,
            prix DOUBLE, loyer DOUBLE
        );
    """)


def record_inventory(
    con: duckdb.DuckDBPyConnection, source: str, urls: set[str], complete: bool,
) -> None:
    """Remplace l'inventaire d'une source par la liste qui vient d'être lue.

    Un sitemap incomplet (erreur réseau sur un fichier) ou anormalement court
    n'efface jamais l'inventaire précédent : il est seulement journalisé.
    """
    ensure_tables(con)
    previous = con.execute("""
        SELECT max(url_count) FROM inventory_runs
        WHERE source=? AND complete AND run_at > now() - INTERVAL 30 DAY
    """, [source]).fetchone()[0]
    trusted = complete and bool(urls) and (not previous or len(urls) >= previous * 0.7)
    con.execute(
        "INSERT INTO inventory_runs VALUES (?, now(), ?, ?)",
        [source, len(urls), trusted],
    )
    if not trusted:
        print(
            f"Inventaire {source} ignoré : {len(urls)} URL "
            f"({'incomplet' if not complete else f'contre {previous} au précédent passage'}).",
            flush=True,
        )
        return
    con.execute("DELETE FROM listing_inventory WHERE source=?", [source])
    # executemany est très lent sur DuckDB : l'inventaire passe par un fichier.
    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as stream:
        stream.write("\n".join(url.replace("\n", "") for url in urls))
        path = stream.name
    try:
        escaped = path.replace("'", "''")
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE inventory_batch AS
            SELECT unnest(string_split(content, chr(10))) AS url FROM read_text('{escaped}')
        """)
    finally:
        Path(path).unlink(missing_ok=True)
    con.execute("""
        INSERT OR REPLACE INTO listing_inventory
        SELECT DISTINCT ?, url, now() FROM inventory_batch WHERE url IS NOT NULL
    """, [source])


def refresh_activity(con: duckdb.DuckDBPyConnection, source: str) -> None:
    """Recalcule `active` pour une source après sa normalisation."""
    ensure_tables(con)
    has_inventory = con.execute("""
        SELECT count(*) FROM inventory_runs WHERE source=? AND complete
    """, [source]).fetchone()[0] > 0
    raw_exists = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name=?", [f"raw_{source}"]
    ).fetchone()[0]
    past_sale = "(date_vente IS NOT NULL AND date_vente < now() - INTERVAL 1 DAY)"
    if has_inventory and raw_exists:
        # L'URL publiée est relue dans Bronze, qui conserve l'URL réellement
        # visitée : certaines sources réécrivent ensuite l'URL canonique.
        con.execute(f"""
            UPDATE annonces_stg AS a
            SET active = NOT {past_sale} AND (
                a.external_id IN (
                    SELECT CAST(r.id AS VARCHAR) FROM raw_{source} r
                    JOIN listing_inventory i ON i.source=? AND i.url=r.url
                )
                OR a.url IN (SELECT url FROM listing_inventory WHERE source=?)
            )
            WHERE a.source=?
        """, [source, source, source])
    else:
        stale_days = int(os.getenv("LISTING_STALE_DAYS", "21"))
        con.execute(f"""
            UPDATE annonces_stg
            SET active = NOT {past_sale}
                AND last_seen_at >= now() - (? * INTERVAL 1 DAY)
            WHERE source=?
        """, [stale_days, source])


def record_prices(con: duckdb.DuckDBPyConnection, source: str | None = None) -> int:
    """Ajoute une ligne d'historique pour chaque prix nouveau ou modifié."""
    ensure_tables(con)
    scope = "AND a.source=?" if source else ""
    params = [source] if source else []
    before = con.execute("SELECT count(*) FROM prix_historique").fetchone()[0]
    con.execute(f"""
        INSERT INTO prix_historique
        SELECT a.source, a.external_id, COALESCE(a.last_seen_at, now()), a.prix, a.loyer
        FROM annonces_stg a
        LEFT JOIN (
            SELECT source, external_id, arg_max(prix, observed_at) AS prix,
                   arg_max(loyer, observed_at) AS loyer
            FROM prix_historique GROUP BY ALL
        ) h ON h.source=a.source AND h.external_id=a.external_id
        WHERE (a.prix IS NOT NULL OR a.loyer IS NOT NULL) {scope}
          AND (h.source IS NULL
               OR h.prix IS DISTINCT FROM a.prix
               OR h.loyer IS DISTINCT FROM a.loyer)
    """, params)
    return con.execute("SELECT count(*) FROM prix_historique").fetchone()[0] - before


PRICE_SIGNALS_SQL = """
    SELECT source, external_id,
           arg_min(prix, observed_at) AS prix_initial,
           max(prix) AS prix_max_observe,
           count(*) FILTER (WHERE baisse) AS nb_baisses,
           max(observed_at) FILTER (WHERE baisse) AS derniere_baisse
    FROM (
        SELECT *, prix < lag(prix) OVER (
            PARTITION BY source, external_id ORDER BY observed_at
        ) AS baisse
        FROM prix_historique WHERE prix > 0
    ) GROUP BY ALL
"""
