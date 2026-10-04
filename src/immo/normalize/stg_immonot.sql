-- Deux formes Bronze coexistent : fiches /annonce-immobiliere/ d'avant la
-- refonte de juillet 2026 et fiches /immobilier-notaire/detail/ actuelles.
MERGE INTO annonces_stg AS target
USING (
    WITH typed AS (
        SELECT *, lower(CAST(url AS VARCHAR)) AS u, lower(CAST(type_hint AS VARCHAR)) AS t,
               lower(CAST(url AS VARCHAR)) LIKE '%/location-fonds-et-ou-murs-commerciaux%' AS rental,
               lower(CAST(type_hint AS VARCHAR))='terrain'
                 OR (COALESCE(lower(CAST(type_hint AS VARCHAR)),'') NOT IN
                       ('maison','appartement','immeuble','local commercial','parking','agricole','autre')
                     AND lower(CAST(url AS VARCHAR)) LIKE '%terrain%') AS is_land
        FROM raw_immonot
        WHERE (url LIKE '%/annonce-immobiliere/%' OR url LIKE '%/immobilier-notaire/detail/%')
          AND (url NOT LIKE '%/location-%' OR url LIKE '%/location-fonds-et-ou-murs-commerciaux%')
    )
    SELECT 'immonot' AS source, CAST(id AS VARCHAR) AS external_id,
           CASE WHEN rental THEN 'location' ELSE 'vente' END AS categorie,
           CASE WHEN t='maison' THEN 'maison' WHEN t='appartement' THEN 'appartement'
                WHEN t='immeuble' THEN 'immeuble' WHEN t='terrain' THEN 'terrain'
                WHEN t='local commercial' THEN 'local_commercial'
                WHEN t IN ('parking','agricole','autre') THEN 'autre'
                -- Forme héritée : type déduit de l'URL ou du JSON-LD.
                WHEN is_land THEN 'terrain'
                WHEN u LIKE '%fonds-et-ou-murs-commerciaux%' THEN 'local_commercial'
                WHEN u LIKE '%bien-agricole%' THEN 'autre'
                WHEN u LIKE '%immeuble%' THEN 'immeuble'
                WHEN u LIKE '%appartement%' THEN 'appartement'
                WHEN u LIKE '%maison%' THEN 'maison'
                WHEN t='apartment' THEN 'appartement'
                WHEN t IN ('house','residence') THEN 'maison'
                WHEN t='localcommercial' THEN 'local_commercial'
                ELSE 'autre' END AS type_bien,
           CASE WHEN rental THEN NULL ELSE TRY_CAST(price AS DOUBLE) END AS prix,
           CASE WHEN rental THEN TRY_CAST(price AS DOUBLE) END AS loyer,
           CASE WHEN is_land THEN NULL ELSE TRY_CAST(surface AS DOUBLE) END AS surface_bati,
           CASE WHEN is_land THEN COALESCE(TRY_CAST(land_surface AS DOUBLE), TRY_CAST(surface AS DOUBLE))
                ELSE TRY_CAST(land_surface AS DOUBLE) END AS surface_terrain,
           CAST(zipcode AS VARCHAR) AS code_postal, CAST(city AS VARCHAR) AS ville,
           TRY_CAST(lat AS DOUBLE) AS lat, TRY_CAST(lng AS DOUBLE) AS lng,
           CASE WHEN lower(COALESCE(CAST(name AS VARCHAR),'')) LIKE 'office notarial%'
                THEN concat(
                    CASE WHEN u LIKE '%terrain%' THEN 'Terrain à bâtir'
                         WHEN u LIKE '%appartement%' THEN 'Appartement'
                         WHEN u LIKE '%maison%' THEN 'Maison'
                         WHEN u LIKE '%fonds-et-ou-murs-commerciaux%' THEN 'Local commercial'
                         ELSE 'Bien immobilier' END,
                    CASE WHEN city IS NOT NULL THEN concat(' à ', city) ELSE '' END
                ) ELSE CAST(name AS VARCHAR) END AS titre,
           CAST(url AS VARCHAR) AS url,
           COALESCE(TRY_CAST(_collected_at AS TIMESTAMP), now()) AS collected_at,
           CAST(body AS VARCHAR) AS description, TRY_CAST(rooms AS INTEGER) AS nb_pieces,
           TRY_CAST(bedrooms AS INTEGER) AS nb_chambres,
           CAST(dpe AS VARCHAR) AS dpe, CAST(ges AS VARCHAR) AS ges,
           'pro' AS seller_type, CAST(seller_name AS VARCHAR) AS seller_name,
           TRY_CAST(image_count AS INTEGER) AS image_count,
           TRY_CAST(published_at AS TIMESTAMP) AS published_at,
           CAST(details_json AS VARCHAR) AS details_json,
           CAST(images_json AS VARCHAR) AS images_json,
           CAST(reference_annonce AS VARCHAR) AS reference_annonce,
           CASE WHEN u LIKE '%/encheres-%interactiv%' THEN 'vente_interactive'
                WHEN u LIKE '%/encheres-%' THEN 'enchere_notariale'
                ELSE 'gre_a_gre' END AS mode_vente
    FROM typed
    WHERE id IS NOT NULL
    QUALIFY row_number() OVER (
        PARTITION BY CAST(id AS VARCHAR)
        ORDER BY COALESCE(TRY_CAST(_collected_at AS TIMESTAMP), now()) DESC
    ) = 1
) AS incoming
ON target.source=incoming.source AND target.external_id=incoming.external_id
WHEN MATCHED THEN UPDATE SET
    categorie=incoming.categorie, type_bien=incoming.type_bien, prix=incoming.prix,
    loyer=incoming.loyer, surface_bati=incoming.surface_bati,
    surface_terrain=incoming.surface_terrain, code_postal=incoming.code_postal,
    ville=incoming.ville, lat=incoming.lat, lng=incoming.lng, titre=incoming.titre,
    url=incoming.url, collected_at=incoming.collected_at, last_seen_at=incoming.collected_at,
    active=true, description=incoming.description, nb_pieces=incoming.nb_pieces,
    nb_chambres=incoming.nb_chambres, dpe=incoming.dpe, ges=incoming.ges,
    seller_type=incoming.seller_type, seller_name=incoming.seller_name,
    image_count=incoming.image_count, published_at=COALESCE(incoming.published_at, target.published_at),
    details_json=incoming.details_json, images_json=incoming.images_json,
    reference_annonce=incoming.reference_annonce, mode_vente=incoming.mode_vente
WHEN NOT MATCHED THEN INSERT (
    source, external_id, categorie, type_bien, prix, loyer, surface_bati,
    surface_terrain, code_postal, ville, lat, lng, titre, url, collected_at,
    first_seen_at, last_seen_at, active, description, nb_pieces, nb_chambres,
    dpe, ges, seller_type, seller_name, image_count, published_at, details_json,
    images_json, reference_annonce, mode_vente
) VALUES (
    incoming.source, incoming.external_id, incoming.categorie, incoming.type_bien,
    incoming.prix, incoming.loyer, incoming.surface_bati, incoming.surface_terrain,
    incoming.code_postal, incoming.ville, incoming.lat, incoming.lng, incoming.titre,
    incoming.url, incoming.collected_at, incoming.collected_at,
    incoming.collected_at, true, incoming.description, incoming.nb_pieces,
    incoming.nb_chambres, incoming.dpe, incoming.ges, incoming.seller_type,
    incoming.seller_name, incoming.image_count, incoming.published_at,
    incoming.details_json, incoming.images_json, incoming.reference_annonce,
    incoming.mode_vente
);
