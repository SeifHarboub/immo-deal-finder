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


MATERIALIZED_DEALS_CTE = "WITH deals AS (SELECT * FROM deal_analysis) "


def _deals_cte(con) -> str:
    exists = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='deal_analysis'"
    ).fetchone()[0]
    if exists:
        return MATERIALIZED_DEALS_CTE
    # Avant la première synchronisation : calcul à la volée, plus lent.
    from immo.analysis import deals_sql
    return "WITH deals AS (" + deals_sql() + " SELECT * FROM deals_final) "


def _json(value: Any) -> Any:
    return jsonable_encoder(value, custom_encoder={datetime: lambda x: x.isoformat(), date: lambda x: x.isoformat()})


def _where(
    q: str | None, sources: list[str], types: list[str], department: str | None,
    city: str | None, postal_code: str | None,
    price_min: float | None, price_max: float | None,
    surface_min: float | None, surface_max: float | None,
    land_min: float | None, land_max: float | None,
    levels: list[str], scored_only: bool,
    strategies: list[str] | None = None, segments: list[str] | None = None,
    yield_min: float | None = None, cashflow_positive: bool = False,
    price_drop: bool = False, auctions: str | None = None,
    new_days: int | None = None, real_rent: bool = False,
    include_inactive: bool = False, include_duplicates: bool = False,
) -> tuple[str, list[Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if not include_inactive:
        clauses.append("active IS NOT false")
    if not include_duplicates:
        clauses.append("rang_doublon = 1")
    if q:
        clauses.append("(titre ILIKE ? OR description ILIKE ? OR ville ILIKE ?)")
        params.extend([f"%{q}%"] * 3)
    for column, values in (("source", sources), ("type_bien", types),
                           ("niveau_affaire", levels), ("strategie", strategies or []),
                           ("segment", segments or [])):
        if values:
            clauses.append(f"{column} IN (" + ",".join("?" for _ in values) + ")")
            params.extend(values)
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
    for expression, operator, value in (
        ("COALESCE(prix, loyer)", ">=", price_min), ("COALESCE(prix, loyer)", "<=", price_max),
        ("surface_bati", ">=", surface_min), ("surface_bati", "<=", surface_max),
        ("surface_terrain", ">=", land_min), ("surface_terrain", "<=", land_max),
        ("rendement_net", ">=", yield_min),
    ):
        if value is not None:
            clauses.append(f"{expression} {operator} ?")
            params.append(value)
    if scored_only:
        clauses.append("score_global IS NOT NULL")
    if cashflow_positive:
        clauses.append("cashflow_mensuel >= 0")
    if price_drop:
        clauses.append("baisse_prix_pct >= 1")
    if real_rent:
        clauses.append("loyer_reel")
    if auctions == "only":
        clauses.append("vente_encheres")
    elif auctions == "exclude":
        clauses.append("NOT vente_encheres")
    if new_days is not None:
        clauses.append("jours_en_ligne <= ?")
        params.append(new_days)
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
    strategie: list[str] = Query(default=[]), segment: list[str] = Query(default=[]),
    yield_min: float | None = None, cashflow_positive: bool = False,
    price_drop: bool = False, auctions: str | None = None,
    new_days: int | None = None, real_rent: bool = False,
    include_inactive: bool = False, include_duplicates: bool = False,
    sort: str = "deal", limit: int = Query(24, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    where, params = _where(
        q, source, type_bien, department, city, postal_code, price_min, price_max,
        surface_min, surface_max, land_min, land_max, level, scored_only,
        strategie, segment, yield_min, cashflow_positive, price_drop, auctions,
        new_days, real_rent, include_inactive, include_duplicates,
    )
    sorts = {
        "deal": "score_global DESC NULLS LAST, CASE confiance WHEN 'fiable' THEN 0 WHEN 'indicative' THEN 1 ELSE 2 END, last_seen_at DESC NULLS LAST",
        "yield": "rendement_net DESC NULLS LAST",
        "cashflow": "cashflow_mensuel DESC NULLS LAST",
        "discount": "decote ASC NULLS LAST",
        "drop": "baisse_prix_pct DESC NULLS LAST",
        "recent": "COALESCE(published_at, first_seen_at) DESC NULLS LAST",
        "price_asc": "prix ASC NULLS LAST",
        "price_desc": "prix DESC NULLS LAST",
        "surface": "surface_bati DESC NULLS LAST",
        "auction_date": "date_vente ASC NULLS LAST",
    }
    order_by = sorts.get(sort, sorts["deal"])
    try:
        with connect() as con:
            deals_cte = _deals_cte(con)
            summary = con.execute(deals_cte + f"""
                SELECT count(*),
                       count(*) FILTER (WHERE niveau_affaire='excellente'),
                       count(*) FILTER (WHERE niveau_affaire='bonne'),
                       count(*) FILTER (WHERE score_global IS NOT NULL),
                       count(*) FILTER (WHERE score_global IS NULL),
                       avg(decote) FILTER (WHERE decote IS NOT NULL),
                       count(*) FILTER (WHERE baisse_prix_pct >= 1),
                       count(*) FILTER (WHERE jours_en_ligne <= 2),
                       median(rendement_net) FILTER (WHERE rendement_net IS NOT NULL)
                FROM deals WHERE {where}
            """, params).fetchone()
            cursor = con.execute(deals_cte + f"""
                SELECT * EXCLUDE (description), left(description, 600) AS description
                FROM deals WHERE {where}
                ORDER BY {order_by}, source, external_id LIMIT ? OFFSET ?
            """, [*params, limit, offset])
            columns = [item[0] for item in cursor.description]
            items = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return _json({
            "items": items,
            "summary": {"total": summary[0], "excellent": summary[1],
                        "good": summary[2], "scored": summary[3],
                        "unscored": summary[4], "average_discount": summary[5],
                        "price_drops": summary[6], "new": summary[7],
                        "median_net_yield": summary[8]},
            "limit": limit, "offset": offset,
            "has_more": offset + len(items) < summary[0],
        })
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Lecture impossible : {exc}") from exc


@app.get("/api/annonces/{source}/{external_id:path}/historique")
def price_history(source: str, external_id: str) -> dict[str, Any]:
    try:
        with connect() as con:
            rows = con.execute("""
                SELECT observed_at, prix, loyer FROM prix_historique
                WHERE source=? AND external_id=? ORDER BY observed_at
            """, [source, external_id]).fetchall()
        return _json({"items": [{"date": r[0], "prix": r[1], "loyer": r[2]} for r in rows]})
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Lecture impossible : {exc}") from exc


@app.get("/api/hypotheses")
def investment_assumptions() -> dict[str, Any]:
    from immo.analysis import assumptions
    return assumptions()


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
            duplicates = []
            if item.get("groupe_doublon") is not None and item.get("nb_publications", 1) > 1:
                duplicates = [
                    {"source": row[0], "external_id": row[1], "url": row[2], "prix": row[3],
                     "first_seen_at": row[4]}
                    for row in con.execute(_deals_cte(con) + """
                        SELECT source, external_id, url, prix, first_seen_at FROM deals
                        WHERE groupe_doublon=? AND NOT (source=? AND external_id=?)
                        ORDER BY prix
                    """, [item["groupe_doublon"], source, external_id]).fetchall()
                ]
            history = con.execute("""
                SELECT observed_at, prix FROM prix_historique
                WHERE source=? AND external_id=? ORDER BY observed_at
            """, [source, external_id]).fetchall() if con.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name='prix_historique'"
            ).fetchone()[0] else []
        return _json({
            "item": item, "recent_sales": sales, "duplicates": duplicates,
            "price_history": [{"date": row[0], "prix": row[1]} for row in history],
        })
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Lecture impossible : {exc}") from exc


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
