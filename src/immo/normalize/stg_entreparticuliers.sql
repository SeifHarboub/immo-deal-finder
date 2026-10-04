-- Le connecteur émet déjà un type_hint canonique ; tout autre libellé devient 'autre'.
MERGE INTO annonces_stg AS target
USING (
    SELECT 'entreparticuliers' AS source, CAST(id AS VARCHAR) AS external_id,
           'vente' AS categorie,
           CASE WHEN lower(CAST(type_hint AS VARCHAR)) IN (
                  'appartement','maison','immeuble','terrain','local_commercial',
                  'fonds_commerce','bureau')
                THEN lower(CAST(type_hint AS VARCHAR)) ELSE 'autre' END AS type_bien,
           TRY_CAST(price AS DOUBLE) AS prix, NULL::DOUBLE AS loyer,
           CASE WHEN lower(CAST(type_hint AS VARCHAR))='terrain' THEN NULL
                ELSE TRY_CAST(surface AS DOUBLE) END AS surface_bati,
           CASE WHEN lower(CAST(type_hint AS VARCHAR))='terrain'
                THEN COALESCE(TRY_CAST(land_surface AS DOUBLE),TRY_CAST(surface AS DOUBLE))
                ELSE TRY_CAST(land_surface AS DOUBLE) END AS surface_terrain,
           CAST(zipcode AS VARCHAR) AS code_postal, CAST(city AS VARCHAR) AS ville,
           TRY_CAST(lat AS DOUBLE) AS lat, TRY_CAST(lng AS DOUBLE) AS lng,
           CAST(name AS VARCHAR) AS titre, url,
           COALESCE(TRY_CAST(_collected_at AS TIMESTAMP),now()) AS collected_at,
           body AS description, TRY_CAST(rooms AS INTEGER) AS nb_pieces,
           TRY_CAST(bedrooms AS INTEGER) AS nb_chambres,
           CAST(dpe AS VARCHAR) AS dpe, CAST(ges AS VARCHAR) AS ges, seller_name,
           TRY_CAST(image_count AS INTEGER) AS image_count,
           TRY_CAST(published_at AS TIMESTAMP) AS published_at,
           CAST(details_json AS VARCHAR) AS details_json,
           CAST(images_json AS VARCHAR) AS images_json,
           CAST(reference_annonce AS VARCHAR) AS reference_annonce
    FROM raw_entreparticuliers
    WHERE id IS NOT NULL
    QUALIFY row_number() OVER (PARTITION BY CAST(id AS VARCHAR)
      ORDER BY COALESCE(TRY_CAST(_collected_at AS TIMESTAMP),now()) DESC)=1
) AS incoming
ON target.source=incoming.source AND target.external_id=incoming.external_id
WHEN MATCHED THEN UPDATE SET
    categorie=incoming.categorie,type_bien=incoming.type_bien,prix=incoming.prix,
    surface_bati=incoming.surface_bati,surface_terrain=incoming.surface_terrain,
    code_postal=incoming.code_postal,ville=incoming.ville,lat=incoming.lat,lng=incoming.lng,
    titre=incoming.titre,url=incoming.url,collected_at=incoming.collected_at,
    last_seen_at=incoming.collected_at,active=true,description=incoming.description,
    nb_pieces=incoming.nb_pieces,nb_chambres=incoming.nb_chambres,dpe=incoming.dpe,ges=incoming.ges,
    seller_name=incoming.seller_name,image_count=incoming.image_count,
    published_at=incoming.published_at,details_json=incoming.details_json,
    images_json=incoming.images_json,reference_annonce=incoming.reference_annonce
WHEN NOT MATCHED THEN INSERT (
    source,external_id,categorie,type_bien,prix,loyer,surface_bati,surface_terrain,
    code_postal,ville,lat,lng,titre,url,collected_at,first_seen_at,last_seen_at,
    active,description,nb_pieces,nb_chambres,dpe,ges,seller_name,image_count,
    published_at,details_json,images_json,reference_annonce
) VALUES (
    incoming.source,incoming.external_id,incoming.categorie,incoming.type_bien,
    incoming.prix,incoming.loyer,incoming.surface_bati,incoming.surface_terrain,
    incoming.code_postal,incoming.ville,incoming.lat,incoming.lng,incoming.titre,
    incoming.url,incoming.collected_at,incoming.collected_at,incoming.collected_at,
    true,incoming.description,incoming.nb_pieces,incoming.nb_chambres,incoming.dpe,incoming.ges,
    incoming.seller_name,incoming.image_count,incoming.published_at,
    incoming.details_json,incoming.images_json,incoming.reference_annonce
);
