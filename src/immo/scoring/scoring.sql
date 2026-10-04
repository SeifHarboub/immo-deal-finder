CREATE OR REPLACE TABLE affaires AS
WITH enriched AS (
    SELECT a.*, r.nb_ventes, r.q1_eur_m2, r.median_eur_m2, r.q3_eur_m2,
           a.prix / NULLIF(a.surface_bati, 0) AS prix_m2,
           ((a.prix / NULLIF(a.surface_bati, 0)) - r.median_eur_m2)
               / NULLIF(r.median_eur_m2, 0) AS decote,
           a.surface_bati * r.q1_eur_m2 AS prix_a_viser
    FROM annonces_stg a
    LEFT JOIN prix_reference r
      ON a.code_postal = r.code_postal
     AND r.type_local = CASE a.type_bien
         WHEN 'appartement' THEN 'Appartement'
         WHEN 'maison' THEN 'Maison' END
    WHERE r.median_eur_m2 IS NOT NULL
), classified AS (
    SELECT *,
        CASE WHEN decote <= -0.25 THEN 'excellente'
             WHEN decote <= -0.15 THEN 'bonne'
             WHEN decote < 0 THEN 'correcte'
             ELSE 'hors_cible' END AS niveau_affaire,
        CASE WHEN decote <= -0.25 THEN '#1a9850'
             WHEN decote <= -0.15 THEN '#91cf60'
             WHEN decote < 0 THEN '#fee08b'
             ELSE '#d73027' END AS couleur,
        CASE WHEN nb_ventes >= 10 THEN 'fiable' ELSE 'indicative' END AS confiance
    FROM enriched
)
SELECT * FROM classified ORDER BY decote ASC;

