MERGE INTO annonces_stg AS target
USING (
    SELECT 'superimmo' AS source, CAST(id AS VARCHAR) AS external_id, 'vente' AS categorie,
           CASE lower(CAST(type_hint AS VARCHAR)) WHEN 'appartement' THEN 'appartement'
             WHEN 'maison' THEN 'maison' WHEN 'terrain' THEN 'terrain'
             WHEN 'immeuble' THEN 'autre' ELSE 'autre' END AS type_bien,
           TRY_CAST(price AS DOUBLE) AS prix, NULL::DOUBLE AS loyer,
           TRY_CAST(surface AS DOUBLE) AS surface_bati,
           TRY_CAST(land_surface AS DOUBLE) AS surface_terrain,
           CAST(zipcode AS VARCHAR) AS code_postal, CAST(city AS VARCHAR) AS ville,
           TRY_CAST(lat AS DOUBLE) AS lat, TRY_CAST(lng AS DOUBLE) AS lng,
           CAST(name AS VARCHAR) AS titre, url,
           COALESCE(TRY_CAST(_collected_at AS TIMESTAMP), now()) AS collected_at,
           body AS description, TRY_CAST(rooms AS INTEGER) AS nb_pieces,
           TRY_CAST(bedrooms AS INTEGER) AS nb_chambres
    FROM raw_superimmo
    QUALIFY row_number() OVER (PARTITION BY CAST(id AS VARCHAR)
      ORDER BY COALESCE(TRY_CAST(_collected_at AS TIMESTAMP), now()) DESC)=1
) AS incoming
ON target.source=incoming.source AND target.external_id=incoming.external_id
WHEN MATCHED THEN UPDATE SET
    categorie=incoming.categorie, type_bien=incoming.type_bien, prix=incoming.prix,
    surface_bati=incoming.surface_bati, surface_terrain=incoming.surface_terrain,
    code_postal=incoming.code_postal, ville=incoming.ville, titre=incoming.titre, url=incoming.url,
    collected_at=incoming.collected_at, last_seen_at=incoming.collected_at, active=true,
    description=incoming.description, nb_pieces=incoming.nb_pieces, nb_chambres=incoming.nb_chambres
WHEN NOT MATCHED THEN INSERT (
    source, external_id, categorie, type_bien, prix, loyer, surface_bati, surface_terrain,
    code_postal, ville, lat, lng, titre, url, collected_at, first_seen_at, last_seen_at,
    active, description, nb_pieces, nb_chambres
) VALUES (
    incoming.source, incoming.external_id, incoming.categorie, incoming.type_bien,
    incoming.prix, incoming.loyer, incoming.surface_bati, incoming.surface_terrain,
    incoming.code_postal, incoming.ville, incoming.lat, incoming.lng, incoming.titre,
    incoming.url, incoming.collected_at, incoming.collected_at, incoming.collected_at,
    true, incoming.description, incoming.nb_pieces, incoming.nb_chambres
);
