-- type_hint = '<categorie> <type_bien>' calculé par le connecteur.
MERGE INTO annonces_stg AS target
USING (
    SELECT 'msimond' AS source, CAST(id AS VARCHAR) AS external_id,
           CASE WHEN split_part(CAST(type_hint AS VARCHAR), ' ', 1) = 'location' THEN 'location' ELSE 'vente' END AS categorie,
           CASE WHEN split_part(CAST(type_hint AS VARCHAR), ' ', 2) IN (
                     'local_commercial', 'fonds_commerce', 'bureau', 'immeuble', 'terrain',
                     'autre', 'appartement', 'maison')
                THEN split_part(CAST(type_hint AS VARCHAR), ' ', 2) ELSE 'autre' END AS type_bien,
           CASE WHEN categorie = 'vente' THEN TRY_CAST(price AS DOUBLE) END AS prix,
           CASE WHEN categorie = 'location' THEN TRY_CAST(price AS DOUBLE) END AS loyer,
           TRY_CAST(surface AS DOUBLE) AS surface_bati,
           TRY_CAST(land_surface AS DOUBLE) AS surface_terrain,
           NULLIF(CAST(zipcode AS VARCHAR), '') AS code_postal, NULLIF(CAST(city AS VARCHAR), '') AS ville,
           TRY_CAST(lat AS DOUBLE) AS lat, TRY_CAST(lng AS DOUBLE) AS lng,
           name AS titre, url,
           COALESCE(TRY_CAST(_collected_at AS TIMESTAMP), now()) AS collected_at,
           body AS description, TRY_CAST(rooms AS INTEGER) AS nb_pieces,
           CAST(dpe AS VARCHAR) AS dpe, CAST(ges AS VARCHAR) AS ges,
           seller_name, TRY_CAST(image_count AS INTEGER) AS image_count,
           TRY_CAST(published_at AS TIMESTAMP) AS published_at,
           CAST(details_json AS VARCHAR) AS details_json,
           CAST(images_json AS VARCHAR) AS images_json,
           CAST(reference_annonce AS VARCHAR) AS reference_annonce
    FROM raw_msimond
    WHERE id IS NOT NULL
    QUALIFY row_number() OVER (
        PARTITION BY CAST(id AS VARCHAR)
        ORDER BY COALESCE(TRY_CAST(_collected_at AS TIMESTAMP), now()) DESC
    ) = 1
) AS incoming
ON target.source = incoming.source AND target.external_id = incoming.external_id
WHEN MATCHED THEN UPDATE SET
    categorie = incoming.categorie, type_bien = incoming.type_bien,
    prix = incoming.prix, loyer = incoming.loyer,
    surface_bati = incoming.surface_bati, surface_terrain = incoming.surface_terrain,
    code_postal = incoming.code_postal, ville = incoming.ville, lat = incoming.lat,
    lng = incoming.lng, titre = incoming.titre, url = incoming.url,
    collected_at = incoming.collected_at, last_seen_at = incoming.collected_at,
    active = true, description = incoming.description, nb_pieces = incoming.nb_pieces,
    dpe = incoming.dpe, ges = incoming.ges,
    seller_name = incoming.seller_name, image_count = incoming.image_count,
    published_at = COALESCE(incoming.published_at, target.published_at),
    details_json = incoming.details_json, images_json = incoming.images_json,
    reference_annonce = incoming.reference_annonce
WHEN NOT MATCHED THEN INSERT (
    source, external_id, categorie, type_bien, prix, loyer, surface_bati,
    surface_terrain, code_postal, ville, lat, lng, titre, url, collected_at,
    first_seen_at, last_seen_at, active, description, nb_pieces, dpe, ges,
    seller_name, image_count, published_at, details_json, images_json, reference_annonce
) VALUES (
    incoming.source, incoming.external_id, incoming.categorie, incoming.type_bien,
    incoming.prix, incoming.loyer, incoming.surface_bati, incoming.surface_terrain,
    incoming.code_postal, incoming.ville, incoming.lat, incoming.lng, incoming.titre,
    incoming.url, incoming.collected_at, incoming.collected_at,
    incoming.collected_at, true, incoming.description, incoming.nb_pieces,
    incoming.dpe, incoming.ges, incoming.seller_name, incoming.image_count,
    incoming.published_at, incoming.details_json, incoming.images_json,
    incoming.reference_annonce
);
