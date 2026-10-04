from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from immo.warehouse import connect


STATIC_DIR = Path(__file__).parent / "static"
app = FastAPI(title="Immo Deal Finder", version="0.2.0", docs_url="/api/docs")


@app.middleware("http")
async def disable_local_frontend_cache(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


DEALS_CTE = """
WITH rent_model AS (
    SELECT a.*,
           r.nb_ventes, r.q1_eur_m2, r.median_eur_m2, r.q3_eur_m2,
           r.premiere_vente, r.derniere_vente,
           r.surface_mediane_reference, r.score_comparabilite,
           rent.loyer_m2_mois, rent.loyer_m2_bas, rent.loyer_m2_haut,
           rent.niveau_estimation AS niveau_estimation_loyer,
           rent.nb_observations AS nb_observations_loyer,
           rent.r2 AS r2_loyer, rent.millesime AS millesime_loyer,
           CASE WHEN a.type_bien='appartement' AND a.nb_pieces BETWEEN 1 AND 2 THEN 37.0
                WHEN a.type_bien='appartement' AND a.nb_pieces>=3 THEN 72.0
                WHEN a.type_bien='appartement' THEN 52.0
                WHEN a.type_bien='maison' THEN 92.0
                ELSE a.surface_bati END AS surface_loyer_reference,
           CASE WHEN a.type_bien='appartement' THEN .75
                WHEN a.type_bien='maison' THEN .80 ELSE 1.0 END AS elasticite_surface_loyer,
           CASE WHEN a.categorie='vente'
                THEN a.prix / NULLIF(a.surface_bati, 0) END AS prix_m2,
           lower(coalesce(a.details_json,'')) LIKE '%approximative%'
                AS localisation_approximative,
           regexp_matches(lower(strip_accents(coalesce(a.titre,'') || ' ' || coalesce(a.description,''))),
                'viager|bouquet|rente viag|nue.?propriete|vente a terme|adjudication|enchere|mise a prix|vente interactive|parts sociales|quote.?part')
                AS transaction_atypique,
           regexp_matches(lower(strip_accents(coalesce(a.titre,'') || ' ' || coalesce(a.description,''))),
                'vendu loue|vendue louee|vendus loues|vendues louees|bien occupe|logement occupe|occupes par un|occupees par un|locataire en place|locataires en place|bail en cours|bail commercial|sous bail')
                AS bien_occupe,
           regexp_matches(lower(strip_accents(coalesce(a.titre,'') || ' ' || coalesce(a.description,''))),
                'ancien garage|garage.?atelier|garages attenants|plateau brut|plateaux.{0,50}a rendre habitable|a rendre habitable|volume brut|local a transformer|transformation complete en espace habitable|a transformer en habitation|changement de destination|raccordement.{0,40}a prevoir|assainissement.{0,40}a prevoir')
                AS bien_non_habitable,
           regexp_matches(lower(strip_accents(coalesce(a.titre,'') || ' ' || coalesce(a.description,''))),
                'residence hoteliere|residence senior|residence seniors|residence services|ehpad|loyer garanti|bail commercial|lmnp')
                AS residence_geree,
           regexp_matches(lower(strip_accents(coalesce(a.titre,'') || ' ' || coalesce(a.description,''))),
                'compromis en cours|sous compromis|sous offre|offre acceptee|vente realisee|bien vendu')
                AS non_disponible,
           regexp_matches(lower(strip_accents(coalesce(a.description,''))),
                'loi carrez.{0,80}loggia|loggia.{0,80}loi carrez|ancienne loggia')
                AS surface_atypique,
           regexp_matches(lower(strip_accents(coalesce(a.titre,'') || ' ' || coalesce(a.description,''))),
                'a renover|a finir de renover|travaux.{0,60}a prevoir|travaux restants|quelques travaux|necessitant.{0,30}travaux|renovation.{0,40}a prevoir|renovation complete|rehabilitation|gros travaux|refection complete|remise au gout')
                AS travaux_probables,
           lower(coalesce(json_extract_string(try_cast(a.details_json AS JSON),
                '$."Procédure de copropriété en cours"'), 'non'))='oui'
                AS procedure_copropriete,
           COALESCE(
             TRY_CAST(json_extract_string(try_cast(a.details_json AS JSON),
                  '$."Charges annuelles de copropriété"') AS DOUBLE),
             12 * TRY_CAST(replace(regexp_extract(coalesce(a.details_json,''),
                  'Charges mensuelles copro : ([0-9 ]+)', 1), ' ', '') AS DOUBLE)
           )
                AS charges_copropriete_annuelles,
           CASE WHEN a.prix IS NULL OR a.prix <= 0 OR a.surface_bati IS NULL OR a.surface_bati <= 0
                     OR (r.median_eur_m2 IS NOT NULL
                         AND a.prix <= a.surface_bati * r.median_eur_m2 * 0.55)
                     OR regexp_matches(lower(strip_accents(coalesce(a.titre,'') || ' ' || coalesce(a.description,''))),
                        'ancien garage|garage.?atelier|garages attenants|plateau brut|plateaux.{0,50}a rendre habitable|a rendre habitable|volume brut|local a transformer|transformation complete en espace habitable|a transformer en habitation|changement de destination|raccordement.{0,40}a prevoir|assainissement.{0,40}a prevoir')
                     OR regexp_matches(lower(strip_accents(coalesce(a.titre,'') || ' ' || coalesce(a.description,''))),
                        'compromis en cours|sous compromis|sous offre|offre acceptee|vente realisee|bien vendu')
                     OR regexp_matches(lower(strip_accents(coalesce(a.titre,'') || ' ' || coalesce(a.description,''))),
                        'viager|bouquet|rente viag|nue.?propriete|vente a terme|adjudication|enchere|mise a prix|vente interactive|parts sociales|quote.?part')
                THEN true ELSE false END AS prix_trop_bas
    FROM annonces_stg a
    LEFT JOIN annonce_reference r
      ON r.source=a.source AND r.external_id=a.external_id
    LEFT JOIN annonce_loyer_reference rent
      ON rent.source=a.source AND rent.external_id=a.external_id
    WHERE a.categorie = 'vente'
       OR a.type_bien IN ('fonds_commerce', 'local_commercial')
), base AS (
    SELECT rent_model.*,
           CASE WHEN type_bien='local_commercial'
                THEN loyer_m2_mois * surface_bati
                ELSE loyer_m2_mois * surface_loyer_reference
                     * pow(greatest(.25, least(3.5, surface_bati/surface_loyer_reference)),
                           elasticite_surface_loyer) END AS loyer_mensuel_estime,
           CASE WHEN type_bien='local_commercial'
                THEN loyer_m2_bas * surface_bati
                ELSE loyer_m2_mois * surface_loyer_reference
                     * pow(greatest(.25, least(3.5, surface_bati/surface_loyer_reference)),
                           elasticite_surface_loyer)
                     * exp(ln(NULLIF(loyer_m2_bas,0)/NULLIF(loyer_m2_mois,0))
                           * .654) END AS loyer_mensuel_bas,
           CASE WHEN type_bien='local_commercial'
                THEN loyer_m2_haut * surface_bati
                ELSE loyer_m2_mois * surface_loyer_reference
                     * pow(greatest(.25, least(3.5, surface_bati/surface_loyer_reference)),
                           elasticite_surface_loyer)
                     * exp(ln(NULLIF(loyer_m2_haut,0)/NULLIF(loyer_m2_mois,0))
                           * .654) END AS loyer_mensuel_haut,
           loyer_m2_bas * surface_bati AS loyer_mensuel_bas_officiel,
           loyer_m2_haut * surface_bati AS loyer_mensuel_haut_officiel
    FROM rent_model
), deals AS (
    SELECT base.*,
           1200 * loyer_mensuel_estime / NULLIF(prix,0) AS rendement_brut_affiche,
           1200 * loyer_mensuel_bas / NULLIF(prix,0) AS rendement_brut_prudent,
           1200 * loyer_mensuel_haut / NULLIF(prix,0) AS rendement_brut_haut,
           1200 * loyer_mensuel_estime / NULLIF(surface_bati*median_eur_m2,0)
                AS rendement_brut_marche,
           CASE WHEN NOT prix_trop_bas
                THEN ((prix / NULLIF(surface_bati, 0)) - median_eur_m2)
                     / NULLIF(median_eur_m2, 0) END AS decote,
           CASE
             WHEN prix IS NULL OR prix <= 0 THEN 'Prix de vente nul ou absent'
             WHEN surface_bati IS NULL OR surface_bati <= 0 THEN 'Surface habitable absente ou incohérente'
             WHEN transaction_atypique THEN 'Mode de vente atypique : comparaison DVF directe non valable'
             WHEN bien_non_habitable THEN 'Le bien n’est pas encore un logement habitable comparable aux ventes DVF résidentielles'
             WHEN non_disponible THEN 'L’annonce indique qu’une offre ou un compromis est déjà en cours'
             WHEN median_eur_m2 IS NOT NULL
                  AND prix <= surface_bati * median_eur_m2 * .55
                  THEN 'Décote supérieure à 45 % : prix, état ou nature de la vente à contrôler'
             ELSE NULL END AS motif_verification,
           concat_ws(' · ',
             CASE WHEN bien_occupe THEN 'Bien vendu occupé' END,
             CASE WHEN residence_geree THEN 'Résidence gérée ou bail commercial' END,
             CASE WHEN surface_atypique THEN 'Surface Carrez différente de la surface annoncée' END,
             CASE WHEN travaux_probables THEN 'Travaux importants probables' END,
             CASE WHEN procedure_copropriete THEN 'Procédure de copropriété en cours' END,
             CASE WHEN charges_copropriete_annuelles IS NOT NULL AND surface_bati>0
                       AND charges_copropriete_annuelles/surface_bati>35
                  THEN 'Charges de copropriété élevées' END,
             CASE WHEN upper(coalesce(dpe,'')) IN ('F','G') THEN 'DPE énergivore' END,
             CASE WHEN type_bien='maison' AND surface_terrain IS NULL THEN 'Terrain non renseigné' END,
             CASE WHEN localisation_approximative THEN 'Localisation approximative' END
           ) AS risques_evaluation,
           CASE WHEN NOT prix_trop_bas THEN surface_bati * q1_eur_m2 END AS plafond_q1,
           CASE WHEN NOT prix_trop_bas THEN surface_bati * median_eur_m2 * 0.75 END AS plafond_excellente,
           CASE WHEN NOT prix_trop_bas THEN surface_bati * median_eur_m2 * 0.85 END AS plafond_bonne,
           CASE
             WHEN categorie <> 'vente' THEN 'a_analyser'
             WHEN median_eur_m2 IS NULL OR prix IS NULL OR surface_bati IS NULL THEN 'a_analyser'
             WHEN prix_trop_bas THEN 'a_verifier'
             WHEN ((prix / NULLIF(surface_bati, 0)) - median_eur_m2)
                    / NULLIF(median_eur_m2, 0) <= -0.25 THEN 'excellente'
             WHEN ((prix / NULLIF(surface_bati, 0)) - median_eur_m2)
                    / NULLIF(median_eur_m2, 0) <= -0.15 THEN 'bonne'
             WHEN ((prix / NULLIF(surface_bati, 0)) - median_eur_m2)
                    / NULLIF(median_eur_m2, 0) < 0 THEN 'correcte'
             ELSE 'hors_cible'
           END AS niveau_affaire,
           CASE WHEN prix_trop_bas THEN 'indisponible'
                WHEN type_bien='local_commercial' THEN 'indicative'
                WHEN categorie <> 'vente' THEN 'indisponible'
                WHEN nb_ventes >= 10 AND score_comparabilite<=.65
                     AND (q3_eur_m2-q1_eur_m2)/NULLIF(median_eur_m2,0)<=.28
                     AND surface_mediane_reference BETWEEN surface_bati*.85 AND surface_bati*1.15
                     AND derniere_vente >= current_date-INTERVAL '2 years'
                     AND NOT localisation_approximative
                     AND NOT bien_occupe AND NOT bien_non_habitable AND NOT residence_geree
                     AND NOT non_disponible AND NOT surface_atypique
                     AND NOT travaux_probables AND NOT procedure_copropriete
                     AND upper(coalesce(dpe,'')) BETWEEN 'A' AND 'E'
                     AND length(coalesce(description,''))>=250
                     AND coalesce(details_json,'{}')<>'{}'
                     AND NOT (charges_copropriete_annuelles IS NOT NULL AND surface_bati>0
                              AND charges_copropriete_annuelles/surface_bati>35)
                     AND upper(coalesce(dpe,'')) NOT IN ('F','G')
                     AND (type_bien<>'maison' OR surface_terrain IS NOT NULL)
                     THEN 'fiable'
                WHEN nb_ventes >= 7 AND score_comparabilite<=1.05
                     AND (q3_eur_m2-q1_eur_m2)/NULLIF(median_eur_m2,0)<=.45
                     THEN 'indicative'
                WHEN nb_ventes IS NOT NULL THEN 'fragile'
                ELSE 'indisponible' END AS confiance,
           CASE WHEN categorie <> 'vente' OR median_eur_m2 IS NULL OR prix_trop_bas THEN NULL
                ELSE round(greatest(0, least(100,
                     -100 * (((prix / NULLIF(surface_bati, 0)) - median_eur_m2)
                     / NULLIF(median_eur_m2, 0)) / 0.25))
                     * CASE
                         WHEN nb_ventes >= 10 AND score_comparabilite<=.65
                           AND (q3_eur_m2-q1_eur_m2)/NULLIF(median_eur_m2,0)<=.28
                           AND surface_mediane_reference BETWEEN surface_bati*.85 AND surface_bati*1.15
                           AND derniere_vente >= current_date-INTERVAL '2 years'
                           AND NOT localisation_approximative
                           AND NOT bien_occupe AND NOT bien_non_habitable AND NOT residence_geree
                           AND NOT non_disponible AND NOT surface_atypique
                           AND NOT travaux_probables AND NOT procedure_copropriete
                           AND upper(coalesce(dpe,'')) BETWEEN 'A' AND 'E'
                           AND length(coalesce(description,''))>=250
                           AND coalesce(details_json,'{}')<>'{}'
                           AND NOT (charges_copropriete_annuelles IS NOT NULL AND surface_bati>0
                                    AND charges_copropriete_annuelles/surface_bati>35)
                           AND upper(coalesce(dpe,'')) NOT IN ('F','G')
                           AND (type_bien<>'maison' OR surface_terrain IS NOT NULL)
                           THEN 1.0
                         WHEN nb_ventes >= 7 AND score_comparabilite<=1.05
                           AND (q3_eur_m2-q1_eur_m2)/NULLIF(median_eur_m2,0)<=.45
                           THEN .75 ELSE .45 END
                ) END AS score_opportunite
    FROM base
)
"""

MATERIALIZED_DEALS_CTE = "WITH deals AS (SELECT * FROM deal_analysis) "


def _deals_cte(con) -> str:
    exists = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='deal_analysis'"
    ).fetchone()[0]
    return MATERIALIZED_DEALS_CTE if exists else DEALS_CTE


def _json(value: Any) -> Any:
    return jsonable_encoder(value, custom_encoder={datetime: lambda x: x.isoformat(), date: lambda x: x.isoformat()})


def _where(
    q: str | None, sources: list[str], types: list[str], department: str | None,
    city: str | None, postal_code: str | None,
    price_min: float | None, price_max: float | None,
    surface_min: float | None, surface_max: float | None,
    land_min: float | None, land_max: float | None,
    levels: list[str], scored_only: bool,
) -> tuple[str, list[Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if q:
        clauses.append("(titre ILIKE ? OR description ILIKE ? OR ville ILIKE ?)")
        params.extend([f"%{q}%"] * 3)
    if sources:
        clauses.append("source IN (" + ",".join("?" for _ in sources) + ")")
        params.extend(sources)
    if types:
        clauses.append("type_bien IN (" + ",".join("?" for _ in types) + ")")
        params.extend(types)
    if department:
        prefix = "20" if department.upper() in {"2A", "2B"} else department.zfill(2)
        clauses.append("code_postal LIKE ?")
        params.append(f"{prefix}%")
    if city:
        clauses.append("lower(strip_accents(trim(ville))) = lower(strip_accents(trim(?)))")
        params.append(city)
    if postal_code:
        clauses.append("code_postal LIKE ?")
        params.append(f"{postal_code}%")
    if price_min is not None:
        clauses.append("COALESCE(prix, loyer) >= ?")
        params.append(price_min)
    if price_max is not None:
        clauses.append("COALESCE(prix, loyer) <= ?")
        params.append(price_max)
    if surface_min is not None:
        clauses.append("surface_bati >= ?")
        params.append(surface_min)
    if surface_max is not None:
        clauses.append("surface_bati <= ?")
        params.append(surface_max)
    if land_min is not None:
        clauses.append("surface_terrain >= ?")
        params.append(land_min)
    if land_max is not None:
        clauses.append("surface_terrain <= ?")
        params.append(land_max)
    if levels:
        clauses.append("niveau_affaire IN (" + ",".join("?" for _ in levels) + ")")
        params.extend(levels)
    if scored_only:
        clauses.append("score_opportunite IS NOT NULL")
    return " AND ".join(clauses), params


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/filters")
def filters() -> dict[str, Any]:
    try:
        with connect() as con:
            sources = [row[0] for row in con.execute(
                """SELECT DISTINCT source FROM annonces_stg
                   WHERE source IS NOT NULL AND (categorie='vente' OR type_bien IN ('fonds_commerce','local_commercial'))
                   ORDER BY source"""
            ).fetchall()]
            types = [row[0] for row in con.execute(
                """SELECT DISTINCT type_bien FROM annonces_stg
                   WHERE type_bien IS NOT NULL AND (categorie='vente' OR type_bien IN ('fonds_commerce','local_commercial'))
                   ORDER BY type_bien"""
            ).fetchall()]
            counts = con.execute("""
                SELECT count(*), count(DISTINCT code_postal), max(last_seen_at),
                       (SELECT count(DISTINCT code_postal) FROM prix_reference)
                FROM annonces_stg
                WHERE categorie='vente' OR type_bien IN ('fonds_commerce','local_commercial')
            """).fetchone()
            price_stats = con.execute("""
                SELECT max(prix), quantile_cont(prix, .99)
                FROM annonces_stg WHERE prix > 0 AND categorie='vente'
            """).fetchone()
            latest_run = con.execute("""
                SELECT source, status, started_at, finished_at, rows_collected
                FROM collection_runs ORDER BY started_at DESC LIMIT 1
            """).fetchone()
        return _json({
            "sources": sources, "types": types, "total": counts[0],
            "postal_codes": counts[1], "last_seen_at": counts[2],
            "reference_count": counts[3], "latest_run": latest_run,
            "price_max_observed": price_stats[0], "price_p99": price_stats[1],
        })
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Base temporairement indisponible : {exc}") from exc


@app.get("/api/cities")
def cities(
    department: str | None = None,
    q: str | None = None,
    limit: int = Query(80, ge=1, le=200),
) -> dict[str, Any]:
    clauses = [
        "ville IS NOT NULL", "trim(ville) <> ''",
        "(categorie='vente' OR type_bien IN ('fonds_commerce','local_commercial'))",
    ]
    params: list[Any] = []
    if department:
        prefix = "20" if department.upper() in {"2A", "2B"} else department.zfill(2)
        clauses.append("code_postal LIKE ?")
        params.append(f"{prefix}%")
    if q:
        clauses.append("lower(strip_accents(ville)) LIKE lower(strip_accents(?))")
        params.append(f"%{q.strip()}%")
    where = " AND ".join(clauses)
    try:
        with connect() as con:
            rows = con.execute(f"""
                SELECT arg_min(trim(ville), trim(ville)=upper(trim(ville))) AS ville,
                       min(code_postal) AS code_postal,
                       count(*) AS annonces
                FROM annonces_stg
                WHERE {where}
                GROUP BY lower(strip_accents(trim(ville)))
                ORDER BY annonces DESC, ville
                LIMIT ?
            """, [*params, limit]).fetchall()
        return _json({"items": [
            {"name": row[0], "postal_code": row[1], "count": row[2]}
            for row in rows
        ]})
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Villes temporairement indisponibles : {exc}") from exc


@app.get("/api/deals")
def deals(
    q: str | None = None,
    source: list[str] = Query(default=[]), type_bien: list[str] = Query(default=[]),
    department: str | None = None, city: str | None = None,
    postal_code: str | None = None,
    price_min: float | None = None, price_max: float | None = None,
    surface_min: float | None = None, surface_max: float | None = None,
    land_min: float | None = None, land_max: float | None = None,
    level: list[str] = Query(default=[]), scored_only: bool = False,
    sort: str = "deal", limit: int = Query(24, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    where, params = _where(
        q, source, type_bien, department, city, postal_code, price_min, price_max,
        surface_min, surface_max, land_min, land_max, level, scored_only,
    )
    sorts = {
        "deal": "CASE confiance WHEN 'fiable' THEN 0 WHEN 'indicative' THEN 1 WHEN 'fragile' THEN 2 ELSE 3 END, score_opportunite DESC NULLS LAST, decote ASC NULLS LAST, last_seen_at DESC NULLS LAST",
        "recent": "COALESCE(published_at, last_seen_at) DESC NULLS LAST",
        "price_asc": "prix ASC NULLS LAST",
        "price_desc": "prix DESC NULLS LAST",
        "surface": "surface_bati DESC NULLS LAST",
    }
    order_by = sorts.get(sort, sorts["deal"])
    try:
        with connect() as con:
            deals_cte = _deals_cte(con)
            summary = con.execute(deals_cte + f"""
                SELECT count(*),
                       count(*) FILTER (WHERE niveau_affaire='excellente' AND confiance='fiable'),
                       count(*) FILTER (WHERE niveau_affaire='bonne' AND confiance='fiable'),
                       count(*) FILTER (WHERE score_opportunite IS NOT NULL),
                       count(*) FILTER (WHERE score_opportunite IS NULL),
                       avg(decote) FILTER (WHERE decote IS NOT NULL)
                FROM deals WHERE {where}
            """, params).fetchone()
            cursor = con.execute(deals_cte + f"""
                SELECT * FROM deals WHERE {where}
                ORDER BY {order_by} LIMIT ? OFFSET ?
            """, [*params, limit, offset])
            columns = [item[0] for item in cursor.description]
            items = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return _json({
            "items": items,
            "summary": {"total": summary[0], "excellent": summary[1],
                        "good": summary[2], "scored": summary[3],
                        "unscored": summary[4], "average_discount": summary[5]},
            "limit": limit, "offset": offset,
            "has_more": offset + len(items) < summary[0],
        })
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Lecture impossible : {exc}") from exc


@app.get("/api/annonces/{source}/{external_id:path}")
def deal_detail(source: str, external_id: str) -> dict[str, Any]:
    try:
        with connect() as con:
            cursor = con.execute(_deals_cte(con) + """
                SELECT * FROM deals WHERE source=? AND external_id=? LIMIT 1
            """, [source, external_id])
            row = cursor.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Annonce introuvable")
            item = dict(zip([column[0] for column in cursor.description], row))
            type_local = {
                "appartement": "Appartement",
                "maison": "Maison",
                "local_commercial": "Local industriel. commercial ou assimilé",
            }.get(item["type_bien"])
            sales = []
            if type_local and item["code_postal"] and item["surface_bati"] and item["nb_ventes"]:
                low_factor, high_factor = {
                    "appartement": (.80, 1.20),
                    "maison": (.75, 1.25),
                    "local_commercial": (.65, 1.35),
                }[item["type_bien"]]
                sales_cursor = con.execute("""
                    WITH subject AS (
                        SELECT ?::DOUBLE AS surface_bati, ?::DOUBLE AS surface_terrain,
                               ?::INTEGER AS nb_pieces, ?::VARCHAR AS code_postal,
                               ?::VARCHAR AS ville, ?::DOUBLE AS lat, ?::DOUBLE AS lng
                    )
                    SELECT v.date_mutation, v.commune, v.adresse, v.surface_bati,
                           v.nombre_pieces, v.surface_terrain, v.valeur_fonciere,
                           v.prix_m2,
                           abs(ln(v.surface_bati/a.surface_bati))*4
                           + CASE
                               WHEN lower(strip_accents(v.commune))=lower(strip_accents(a.ville)) THEN 0
                               WHEN v.code_postal=a.code_postal THEN .35 ELSE 1 END
                           + CASE WHEN a.nb_pieces IS NOT NULL AND v.nombre_pieces IS NOT NULL
                               THEN least(abs(v.nombre_pieces-a.nb_pieces),4)*.12 ELSE .12 END
                           + CASE WHEN a.surface_terrain IS NOT NULL AND v.surface_terrain IS NOT NULL
                               THEN least(abs(ln((v.surface_terrain+1)/(a.surface_terrain+1))),2)*.20 ELSE .30 END
                           + CASE WHEN a.lat IS NOT NULL AND a.lng IS NOT NULL
                                      AND v.latitude IS NOT NULL AND v.longitude IS NOT NULL
                               THEN least(2.0, 111.2*sqrt(pow(v.latitude-a.lat,2)
                                    +pow((v.longitude-a.lng)*cos(radians(a.lat)),2))/10.0)*.35
                               ELSE .10 END
                           + greatest(0,date_diff('day',v.date_mutation,current_date))/365.25*.06
                             AS distance_comparabilite
                    FROM ventes_dvf v CROSS JOIN subject a
                    WHERE v.type_local=?
                      AND v.date_mutation>=current_date-INTERVAL '5 years'
                      AND v.surface_bati BETWEEN a.surface_bati*? AND a.surface_bati*?
                      AND (a.nb_pieces IS NULL OR v.nombre_pieces IS NULL
                           OR abs(v.nombre_pieces-a.nb_pieces)<=2)
                      AND (?<>'maison' OR a.surface_terrain IS NULL OR v.surface_terrain IS NULL
                           OR v.surface_terrain BETWEEN a.surface_terrain*.55 AND a.surface_terrain*1.80)
                      AND (v.code_postal=a.code_postal OR (
                           a.lat IS NOT NULL AND a.lng IS NOT NULL
                           AND v.latitude IS NOT NULL AND v.longitude IS NOT NULL
                           AND 111.2*sqrt(pow(v.latitude-a.lat,2)
                               +pow((v.longitude-a.lng)*cos(radians(a.lat)),2))<=20
                      ))
                    ORDER BY distance_comparabilite LIMIT 12
                """, [
                    item["surface_bati"], item["surface_terrain"], item["nb_pieces"],
                    item["code_postal"], item["ville"], item["lat"], item["lng"],
                    type_local, low_factor, high_factor, item["type_bien"],
                ])
                sales = [dict(zip([c[0] for c in sales_cursor.description], sale))
                         for sale in sales_cursor.fetchall()]
        return _json({"item": item, "recent_sales": sales})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Lecture impossible : {exc}") from exc


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
