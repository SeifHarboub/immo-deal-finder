"""Matérialisation du meilleur indicateur ANIL pour chaque annonce."""

from __future__ import annotations

import duckdb


FINGERPRINT = "hash(a.type_bien,a.nb_pieces,a.surface_bati,a.code_postal,a.ville,f.segment)"


def refresh_rent_estimates(con: duckdb.DuckDBPyConnection, source: str | None = None) -> int:
    source_clause = "AND a.source=?" if source else ""
    params = [source] if source else []
    con.execute(f"""
        DELETE FROM annonce_loyer_reference ref
        USING annonces_stg a LEFT JOIN annonce_finance f USING (source, external_id)
        WHERE ref.source=a.source AND ref.external_id=a.external_id
          {source_clause} AND ref.fingerprint <> {FINGERPRINT}
    """, params)
    before = con.execute("SELECT count(*) FROM annonce_loyer_reference").fetchone()[0]
    con.execute(f"""
        INSERT INTO annonce_loyer_reference
        WITH pending AS MATERIALIZED (
            SELECT a.*, {FINGERPRINT} AS rent_fingerprint,
                   -- Un immeuble de rapport se loue appartement par appartement.
                   CASE WHEN f.segment='immeuble' THEN 'appartement' ELSE a.type_bien END
                       AS type_loyer
            FROM annonces_stg a
            LEFT JOIN annonce_finance f USING (source, external_id)
            WHERE a.categorie='vente'
              AND (a.type_bien IN ('appartement','maison') OR f.segment='immeuble')
              AND a.surface_bati>0 AND a.code_postal IS NOT NULL
              {source_clause}
              AND NOT EXISTS (
                  SELECT 1 FROM annonce_loyer_reference e
                  WHERE e.source=a.source AND e.external_id=a.external_id
                    AND e.fingerprint={FINGERPRINT}
              )
        )
        SELECT a.source,a.external_id,a.rent_fingerprint,
               l.loyer_m2_mois,l.loyer_m2_bas,l.loyer_m2_haut,
               l.niveau_estimation,l.nb_observations,l.r2,l.millesime,now()
        FROM pending a LEFT JOIN LATERAL (
            SELECT r.* FROM loyer_reference_commune r
            WHERE r.type_bien=a.type_loyer
              AND r.typologie=CASE
                  WHEN a.type_bien='appartement' AND a.nb_pieces BETWEEN 1 AND 2 THEN '1_2_pieces'
                  WHEN a.type_bien='appartement' AND a.nb_pieces>=3 THEN '3_pieces_plus'
                  ELSE 'tous' END
              AND (r.code_postal=a.code_postal OR (
                  r.code_postal IS NULL AND left(r.code_commune,2)=left(a.code_postal,2)
                  AND lower(strip_accents(r.commune))=lower(strip_accents(a.ville))))
            ORDER BY CASE WHEN lower(strip_accents(r.commune))=lower(strip_accents(a.ville))
                          THEN 0 ELSE 1 END
            LIMIT 1
        ) l ON true
    """, params)
    # Murs commerciaux : estimation distincte à partir des locations pures
    # réellement collectées, uniquement dans la même ville/zone postale et
    # avec une surface comparable. Trois références minimum sont exigées.
    con.execute(f"""
        INSERT INTO annonce_loyer_reference
        WITH pending AS MATERIALIZED (
            SELECT a.*, {FINGERPRINT} AS rent_fingerprint
            FROM annonces_stg a
            LEFT JOIN annonce_finance f USING (source, external_id)
            WHERE a.categorie='vente' AND a.type_bien IN ('local_commercial','bureau')
              AND coalesce(f.segment,'murs')='murs'
              AND a.surface_bati>0 AND a.code_postal IS NOT NULL
              {source_clause}
              AND NOT EXISTS (
                  SELECT 1 FROM annonce_loyer_reference e
                  WHERE e.source=a.source AND e.external_id=a.external_id
                    AND e.fingerprint={FINGERPRINT}
              )
        )
        SELECT a.source,a.external_id,a.rent_fingerprint,
               l.loyer_m2_mois,l.loyer_m2_bas,l.loyer_m2_haut,
               'annonces_professionnelles' AS niveau_estimation,
               l.nb_observations,NULL::DOUBLE AS r2,
               year(current_date) AS millesime,now()
        FROM pending a LEFT JOIN LATERAL (
            SELECT median(ratio) AS loyer_m2_mois,
                   quantile_cont(ratio,.25) AS loyer_m2_bas,
                   quantile_cont(ratio,.75) AS loyer_m2_haut,
                   count(*)::INTEGER AS nb_observations
            FROM (
                SELECT r.loyer/r.surface_bati AS ratio
                FROM annonces_stg r
                WHERE r.categorie='location' AND r.type_bien=a.type_bien
                  AND r.loyer>0 AND r.surface_bati>0
                  AND r.loyer/r.surface_bati BETWEEN 2 AND 200
                  AND (r.details_json ILIKE '%Location pure%' OR r.source IN ('geolocaux','bureauxlocaux'))
                  AND r.last_seen_at >= now() - INTERVAL 18 MONTH
                  AND r.surface_bati BETWEEN a.surface_bati*.40 AND a.surface_bati*2.50
                  AND (r.code_postal=a.code_postal
                       OR lower(strip_accents(r.ville))=lower(strip_accents(a.ville)))
                ORDER BY CASE WHEN r.code_postal=a.code_postal THEN 0 ELSE 1 END,
                         abs(ln(r.surface_bati/a.surface_bati))
                LIMIT 20
            ) nearby
            HAVING count(*)>=3
        ) l ON true
    """, params)
    return con.execute("SELECT count(*) FROM annonce_loyer_reference").fetchone()[0] - before
