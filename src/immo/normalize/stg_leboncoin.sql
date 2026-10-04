MERGE INTO annonces_stg AS target
USING (
SELECT 'leboncoin' AS source, CAST(list_id AS VARCHAR) AS external_id, 'vente' AS categorie,
    CASE CAST(attr_real_estate_type AS VARCHAR)
        WHEN '1' THEN 'maison' WHEN '2' THEN 'appartement'
        WHEN '3' THEN 'terrain' WHEN '4' THEN 'local_commercial'
        WHEN '5' THEN 'local_commercial' ELSE 'autre' END AS type_bien,
    TRY_CAST(price AS DOUBLE) AS prix, NULL::DOUBLE AS loyer,
    TRY_CAST(attr_square AS DOUBLE) AS surface_bati,
    TRY_CAST(attr_land AS DOUBLE) AS surface_terrain,
    zipcode AS code_postal, city AS ville, TRY_CAST(lat AS DOUBLE) AS lat,
    TRY_CAST(lng AS DOUBLE) AS lng, subject AS titre, url,
    COALESCE(TRY_CAST(_collected_at AS TIMESTAMP), now()) AS collected_at,
    body AS description, TRY_CAST(attr_rooms AS INTEGER) AS nb_pieces,
    TRY_CAST(attr_bedrooms AS INTEGER) AS nb_chambres,
    CAST(attr_energy_rate AS VARCHAR) AS dpe, CAST(attr_ges AS VARCHAR) AS ges,
    seller_type, seller_name, TRY_CAST(image_count AS INTEGER) AS image_count,
    TRY_CAST(first_publication_date AS TIMESTAMP) AS published_at
FROM raw_leboncoin
QUALIFY row_number() OVER (
    PARTITION BY CAST(list_id AS VARCHAR)
    ORDER BY COALESCE(TRY_CAST(_collected_at AS TIMESTAMP), now()) DESC
) = 1
) AS incoming
ON target.source = incoming.source AND target.external_id = incoming.external_id
WHEN MATCHED THEN UPDATE SET
    categorie=incoming.categorie, type_bien=incoming.type_bien, prix=incoming.prix,
    loyer=incoming.loyer, surface_bati=incoming.surface_bati,
    surface_terrain=incoming.surface_terrain, code_postal=incoming.code_postal,
    ville=incoming.ville, lat=incoming.lat, lng=incoming.lng, titre=incoming.titre,
    url=incoming.url, collected_at=incoming.collected_at,
    last_seen_at=incoming.collected_at, active=true,
    description=incoming.description, nb_pieces=incoming.nb_pieces,
    nb_chambres=incoming.nb_chambres, dpe=incoming.dpe, ges=incoming.ges,
    seller_type=incoming.seller_type, seller_name=incoming.seller_name,
    image_count=incoming.image_count, published_at=incoming.published_at
WHEN NOT MATCHED THEN INSERT (
    source, external_id, categorie, type_bien, prix, loyer, surface_bati,
    surface_terrain, code_postal, ville, lat, lng, titre, url, collected_at,
    first_seen_at, last_seen_at, active, description, nb_pieces, nb_chambres,
    dpe, ges, seller_type, seller_name, image_count, published_at
) VALUES (
    incoming.source, incoming.external_id, incoming.categorie, incoming.type_bien,
    incoming.prix, incoming.loyer, incoming.surface_bati, incoming.surface_terrain,
    incoming.code_postal, incoming.ville, incoming.lat, incoming.lng, incoming.titre,
    incoming.url, incoming.collected_at, incoming.collected_at, incoming.collected_at, true,
    incoming.description, incoming.nb_pieces, incoming.nb_chambres, incoming.dpe,
    incoming.ges, incoming.seller_type, incoming.seller_name, incoming.image_count,
    incoming.published_at
);
