"""Recherches sauvegardées depuis le tableau de bord et alertes associées.

Une recherche conserve la chaîne de filtres de l'interface. À chaque
synchronisation, les annonces apparues ou dont le prix a baissé depuis la
dernière alerte et qui correspondent aux filtres déclenchent une notification.
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

import duckdb

from immo.filters import where_from_query


def ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS recherches_sauvegardees (
            id VARCHAR PRIMARY KEY, nom VARCHAR, filtres VARCHAR,
            alerte BOOLEAN, created_at TIMESTAMP, derniere_alerte TIMESTAMP
        )
    """)


# Lors du premier chargement d'une source, toutes ses annonces apparaissent en
# même temps : elles ne sont « nouvelles » qu'au-delà d'un jour de collecte.
NOUVELLE = """first_seen_at >= {since} AND first_seen_at > (
    SELECT min(s.first_seen_at) + INTERVAL 1 DAY FROM annonces_stg s
    WHERE s.source = deal_analysis.source)"""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def save(con: duckdb.DuckDBPyConnection, nom: str, filtres: str, alerte: bool = True) -> str:
    ensure_table(con)
    where_from_query(filtres)  # refuse une chaîne de filtres illisible avant l'écriture
    search_id = uuid.uuid4().hex[:12]
    con.execute(
        "INSERT INTO recherches_sauvegardees VALUES (?, ?, ?, ?, ?, ?)",
        [search_id, nom.strip()[:120] or "Recherche", filtres, alerte, _now(), _now()],
    )
    return search_id


def delete(con: duckdb.DuckDBPyConnection, search_id: str) -> None:
    ensure_table(con)
    con.execute("DELETE FROM recherches_sauvegardees WHERE id=?", [search_id])


def matches_since(con: duckdb.DuckDBPyConnection, filtres: str, since: datetime) -> list[tuple]:
    """Nouvelles annonces ou baisses de prix depuis `since` qui satisfont les filtres."""
    clause, params = where_from_query(filtres)
    return con.execute(f"""
        SELECT source, external_id, titre, ville, prix, round(score_global), strategie,
               first_seen_at >= ? AS nouvelle
        FROM deal_analysis
        WHERE {clause}
          AND (({NOUVELLE.format(since="?")})
               OR (derniere_baisse IS NOT NULL AND derniere_baisse >= ?))
        ORDER BY score_global DESC NULLS LAST
    """, [since, *params, since, since]).fetchall()


def list_with_counts(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Recherches avec le nombre d'annonces nouvelles ou baissées depuis 48 h."""
    ensure_table(con)
    has_analysis = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='deal_analysis'"
    ).fetchone()[0]
    items = []
    for search_id, nom, filtres, alerte, created_at, derniere in con.execute("""
        SELECT id, nom, filtres, alerte, created_at, derniere_alerte
        FROM recherches_sauvegardees ORDER BY created_at
    """).fetchall():
        recent = 0
        total = 0
        if has_analysis:
            clause, params = where_from_query(filtres)
            total, recent = con.execute(f"""
                SELECT count(*), count(*) FILTER (WHERE ({NOUVELLE.format(since="now() - INTERVAL 2 DAY")})
                       OR derniere_baisse >= now() - INTERVAL 2 DAY)
                FROM deal_analysis WHERE {clause}
            """, params).fetchone()
        items.append({
            "id": search_id, "nom": nom, "filtres": filtres, "alerte": alerte,
            "created_at": created_at, "derniere_alerte": derniere,
            "total": total, "recentes": recent,
        })
    return items


def pending_alerts(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Calcule les alertes de chaque recherche et avance son horodatage."""
    ensure_table(con)
    alerts = []
    for search_id, nom, filtres, derniere in con.execute("""
        SELECT id, nom, filtres, derniere_alerte FROM recherches_sauvegardees WHERE alerte
    """).fetchall():
        started = _now()
        rows = matches_since(con, filtres, derniere)
        con.execute("UPDATE recherches_sauvegardees SET derniere_alerte=? WHERE id=?", [started, search_id])
        if rows:
            alerts.append({"id": search_id, "nom": nom, "annonces": rows})
    return alerts
