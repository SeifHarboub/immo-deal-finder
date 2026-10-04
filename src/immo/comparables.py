"""Sélection matérialisée des ventes DVF réellement comparables à chaque annonce."""

from __future__ import annotations

import duckdb


FINGERPRINT = """hash(
    'dvf-comparables-v5',
    a.categorie, a.type_bien, a.surface_bati, a.surface_terrain,
    a.nb_pieces, a.code_postal, a.ville, a.lat, a.lng,
    lower(coalesce(a.details_json,'')) LIKE '%approximative%'
)"""


def ensure_price_index(con: duckdb.DuckDBPyConnection, rebuild: bool = False) -> None:
    """Indice de prix DVF par département, type et année.

    Les comparables remontent jusqu'à quatre ans ; sans correction, la baisse
    des prix depuis 2022 (jusqu'à −12 % sur les appartements des grandes villes)
    passait pour une décote. Chaque vente est ramenée au niveau de la dernière
    année disponible de son département.
    """
    exists = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='dvf_indice'"
    ).fetchone()[0]
    if exists and not rebuild:
        return
    con.execute("""
        CREATE OR REPLACE TABLE dvf_indice AS
        WITH annuel AS (
            SELECT code_departement, type_local, year(date_mutation) AS annee,
                   median(prix_m2) AS mediane, count(*) AS ventes
            FROM ventes_dvf
            WHERE type_local IN ('Appartement', 'Maison') AND prix_m2 > 0
            GROUP BY ALL HAVING count(*) >= 150
        )
        SELECT a.code_departement, a.type_local, a.annee,
               greatest(.8, least(1.25, last.mediane / a.mediane)) AS ratio
        FROM annuel a
        JOIN (SELECT code_departement, type_local, arg_max(mediane, annee) AS mediane
              FROM annuel GROUP BY ALL) last
          USING (code_departement, type_local)
    """)


def refresh_comparables(
    con: duckdb.DuckDBPyConnection,
    source: str | None = None,
    external_id: str | None = None,
) -> int:
    """Calcule uniquement les références absentes ou devenues obsolètes."""
    scope = []
    params = []
    if source:
        scope.append("a.source=?")
        params.append(source)
    if external_id:
        scope.append("a.external_id=?")
        params.append(external_id)
    source_clause = "AND " + " AND ".join(scope) if scope else ""
    ensure_price_index(con)
    con.execute(f"""
        DELETE FROM annonce_reference AS ref
        USING annonces_stg AS a
        WHERE ref.source=a.source AND ref.external_id=a.external_id
          {source_clause}
          AND ref.fingerprint <> {FINGERPRINT}
    """, params)
    before = con.execute("SELECT count(*) FROM annonce_reference").fetchone()[0]
    con.execute(f"""
        INSERT INTO annonce_reference
        WITH pending AS MATERIALIZED (
            SELECT a.*, {FINGERPRINT} AS comparison_fingerprint,
                   lower(coalesce(a.details_json,'')) LIKE '%approximative%'
                       AS location_is_approx
            FROM annonces_stg a
            WHERE a.categorie='vente'
              AND a.type_bien IN ('appartement','maison','local_commercial')
              AND a.surface_bati>0
              {source_clause}
              AND NOT EXISTS (
                  SELECT 1 FROM annonce_reference existing
                  WHERE existing.source=a.source
                    AND existing.external_id=a.external_id
                    AND existing.fingerprint={FINGERPRINT}
              )
        )
        SELECT a.source, a.external_id, a.comparison_fingerprint AS fingerprint,
               r.nb_ventes, r.q1_eur_m2, r.median_eur_m2, r.q3_eur_m2,
               r.premiere_vente, r.derniere_vente,
               r.surface_mediane_reference, r.score_comparabilite, now()
        FROM pending a
        LEFT JOIN LATERAL (
            SELECT count(*) AS nb_ventes,
                   quantile_cont(prix_m2, .25) AS q1_eur_m2,
                   quantile_cont(prix_m2, .50) AS median_eur_m2,
                   quantile_cont(prix_m2, .75) AS q3_eur_m2,
                   min(date_mutation) AS premiere_vente,
                   max(date_mutation) AS derniere_vente,
                   quantile_cont(surface_bati, .50) AS surface_mediane_reference,
                   avg(distance_comparabilite) AS score_comparabilite
            FROM (
                SELECT * EXCLUDE (raw_q1, raw_q3)
                FROM (
                SELECT ranked.*,
                       quantile_cont(prix_m2,.25) OVER () AS raw_q1,
                       quantile_cont(prix_m2,.75) OVER () AS raw_q3
                FROM (
                SELECT v.* REPLACE (v.prix_m2 * coalesce(idx.ratio, 1) AS prix_m2),
                       abs(ln(v.surface_bati/a.surface_bati))*4
                       + CASE
                           WHEN lower(strip_accents(v.commune))=lower(strip_accents(a.ville)) THEN 0
                           WHEN v.code_postal=a.code_postal THEN .35 ELSE 1
                         END
                       + CASE
                           WHEN a.nb_pieces IS NOT NULL AND v.nombre_pieces IS NOT NULL
                             THEN least(abs(v.nombre_pieces-a.nb_pieces),4)*.12
                           ELSE .12
                         END
                       + CASE
                           WHEN a.surface_terrain IS NOT NULL AND v.surface_terrain IS NOT NULL
                             THEN least(abs(ln((v.surface_terrain+1)/(a.surface_terrain+1))),2)*.20
                           ELSE .30
                         END
                       + CASE WHEN NOT a.location_is_approx
                                    AND a.lat IS NOT NULL AND a.lng IS NOT NULL
                                    AND v.latitude IS NOT NULL AND v.longitude IS NOT NULL
                              THEN least(2.0, 111.2*sqrt(
                                   pow(v.latitude-a.lat,2)
                                   + pow((v.longitude-a.lng)*cos(radians(a.lat)),2)
                              )/5.0)*.35 ELSE .15 END
                       + greatest(0,date_diff('day',v.date_mutation,current_date))
                         /365.25*.08 AS distance_comparabilite
                FROM ventes_dvf v
                LEFT JOIN dvf_indice idx
                  ON idx.code_departement=v.code_departement AND idx.type_local=v.type_local
                 AND idx.annee=year(v.date_mutation)
                WHERE v.type_local=CASE a.type_bien
                        WHEN 'appartement' THEN 'Appartement'
                        WHEN 'maison' THEN 'Maison'
                        WHEN 'local_commercial' THEN 'Local industriel. commercial ou assimilé' END
                  AND v.date_mutation>=current_date-INTERVAL '4 years'
                  AND v.surface_bati BETWEEN
                        a.surface_bati*CASE a.type_bien
                            WHEN 'appartement' THEN .85 WHEN 'maison' THEN .80 ELSE .70 END
                        AND a.surface_bati*CASE a.type_bien
                            WHEN 'appartement' THEN 1.15 WHEN 'maison' THEN 1.20 ELSE 1.30 END
                  AND (a.nb_pieces IS NULL OR v.nombre_pieces IS NULL
                       OR abs(v.nombre_pieces-a.nb_pieces)<=1)
                  AND (a.type_bien<>'maison' OR a.surface_terrain IS NULL
                       OR v.surface_terrain IS NULL
                       OR v.surface_terrain BETWEEN a.surface_terrain*.65 AND a.surface_terrain*1.55)
                  AND (v.code_postal=a.code_postal OR (
                       NOT a.location_is_approx
                       AND a.lat IS NOT NULL AND a.lng IS NOT NULL
                       AND v.latitude IS NOT NULL AND v.longitude IS NOT NULL
                       AND 111.2*sqrt(
                           pow(v.latitude-a.lat,2)
                           + pow((v.longitude-a.lng)*cos(radians(a.lat)),2)
                       )<=CASE a.type_bien
                            WHEN 'appartement' THEN 5
                            WHEN 'maison' THEN 12 ELSE 15 END
                  ))
                ORDER BY distance_comparabilite
                LIMIT 15
                ) ranked
                ) with_bounds
                WHERE prix_m2 BETWEEN
                      raw_q1 - 1.5*(raw_q3-raw_q1)
                      AND raw_q3 + 1.5*(raw_q3-raw_q1)
            ) closest_sales
            HAVING count(*)>=5
        ) r ON true
    """, params)
    after = con.execute("SELECT count(*) FROM annonce_reference").fetchone()[0]
    return after - before
