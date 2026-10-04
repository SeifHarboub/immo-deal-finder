"""Filtres communs au tableau de bord et aux recherches sauvegardées."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs


def where(
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


LIST_KEYS = {"source", "type_bien", "level", "strategie", "segment"}
FLOAT_KEYS = {"price_min", "price_max", "surface_min", "surface_max", "land_min", "land_max", "yield_min"}
BOOL_KEYS = {"scored_only", "cashflow_positive", "price_drop", "real_rent", "include_inactive", "include_duplicates"}


def where_from_query(query: str) -> tuple[str, list[Any]]:
    """Reconstruit les filtres d'une recherche enregistrée depuis sa chaîne d'URL."""
    raw = parse_qs(query or "", keep_blank_values=False)
    first = lambda key: raw.get(key, [None])[0]  # noqa: E731
    def number(key: str) -> float | None:
        try:
            return float(first(key)) if first(key) not in (None, "") else None
        except ValueError:
            return None
    new_days = number("new_days")
    return where(
        first("q"), raw.get("source", []), raw.get("type_bien", []), first("department"),
        first("city"), first("postal_code"), number("price_min"), number("price_max"),
        number("surface_min"), number("surface_max"), number("land_min"), number("land_max"),
        raw.get("level", []), first("scored_only") == "true",
        raw.get("strategie", []), raw.get("segment", []), number("yield_min"),
        first("cashflow_positive") == "true", first("price_drop") == "true", first("auctions"),
        int(new_days) if new_days is not None else None, first("real_rent") == "true",
        first("include_inactive") == "true", first("include_duplicates") == "true",
    )
