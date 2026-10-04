"""Extraction des montants financiers publiés dans les annonces.

Les plateformes publient rarement loyer, rendement, chiffre d'affaires ou EBE
dans un champ structuré : ils sont écrits dans la description. Ce module lit
d'abord les champs structurés connus, puis le texte, et ne retient une valeur
que si elle est cohérente avec le prix. Un montant non trouvé reste NULL.

Le résultat est matérialisé dans `annonce_finance` avec une empreinte : seules
les annonces nouvelles ou modifiées sont relues à chaque synchronisation.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import json
from pathlib import Path
import re
import tempfile
import unicodedata

import duckdb


PARSER_VERSION = 6

_AMOUNT = r"(\d{1,3}(?:[  .]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)\s*(k|m|millions?|milliers?)?\s*(?:€|euros?|eur\b|(?<=\d)e\b)"
_AMOUNT_RE = re.compile(_AMOUNT, re.I)
_MONTHLY = re.compile(r"^.{0,25}?(/\s*mois|par mois|mensuel|mois\b|/m\b|mensuellement)", re.I)
_YEARLY = re.compile(r"^.{0,25}?(/\s*an\b|par an\b|annuel|/an|l.an\b|annee|année|ht/an|hc/an)", re.I)


def _plain(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return value.lower().replace("\xa0", " ").replace(" ", " ")


def parse_amount(number: str, unit: str | None) -> float | None:
    raw = number.strip()
    if re.fullmatch(r"\d{1,3}(?:[ .]\d{3})+(?:,\d+)?", raw):
        raw = raw.replace(" ", "").replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(" ", "").replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        return None
    unit = (unit or "").lower()
    if unit.startswith("k") or unit.startswith("millier"):
        value *= 1_000
    elif unit.startswith("m"):
        value *= 1_000_000
    return value


def _amounts_after(text: str, keyword: re.Pattern, window: int = 90):
    """Montants situés juste après un mot-clé, avec le contexte qui suit."""
    for match in keyword.finditer(text):
        segment = text[match.end():match.end() + window]
        amount = _AMOUNT_RE.search(segment)
        if not amount:
            continue
        # Un autre mot-clé financier entre les deux signifie que le montant
        # appartient à une autre grandeur (« loyer ... prix de vente 200 000 € »).
        between = segment[:amount.start()]
        if re.search(r"prix|honoraires|frais|depot|caution|charges|taxe|bouquet|valeur", between):
            continue
        value = parse_amount(amount.group(1), amount.group(2))
        if value:
            yield value, segment[amount.end():amount.end() + 30], between


def _rent(text: str, price: float | None) -> float | None:
    """Loyer annuel encaissé ou payé, d'après le texte."""
    keyword = re.compile(
        r"(?<!sans )(?<!hors )\b(loyers?|revenus? locatifs?|revenu locatif|loue|louee|loues|louees|rapportant|rapporte)\b"
        r"(?!\s+(?:potentiel|estime|envisage|possible|de marche))",
    )
    candidates: list[float] = []
    for value, after, before in _amounts_after(text, keyword):
        context = before + after
        if re.search(r"potentiel|estim|possible|envisageable|pourrait|marche", context):
            continue
        if _MONTHLY.search(before + " " + after) or re.search(r"mensuel", before):
            annual = value * 12
        elif _YEARLY.search(before + " " + after) or re.search(r"annuel", before):
            annual = value
        elif price:
            # Sans unité explicite, retenir l'interprétation plausible.
            monthly_yield = value * 12 / price
            yearly_yield = value / price
            if 0.025 <= monthly_yield <= 0.20 and not 0.025 <= yearly_yield <= 0.20:
                annual = value * 12
            elif 0.025 <= yearly_yield <= 0.20 and not 0.025 <= monthly_yield <= 0.20:
                annual = value
            else:
                continue
        else:
            continue
        candidates.append(annual)
    if not candidates:
        return None
    # Plusieurs lots : la somme est souvent annoncée en dernier (« soit un
    # total de ... »). Le plus grand montant plausible est le loyer global.
    plausible = [value for value in candidates if not price or 0.015 <= value / price <= 0.25]
    return max(plausible) if plausible else None


def _percent(text: str, keyword: str) -> float | None:
    pattern = re.compile(
        rf"(?:{keyword})[^%\d]{{0,40}}?(\d{{1,2}}(?:[.,]\d{{1,2}})?)\s*%"
        rf"|(\d{{1,2}}(?:[.,]\d{{1,2}})?)\s*%[^.\n]{{0,25}}?(?:{keyword})",
    )
    for match in pattern.finditer(text):
        value = float((match.group(1) or match.group(2)).replace(",", "."))
        context = text[max(0, match.start() - 30):match.end() + 30]
        if re.search(r"potentiel|possible|pourrait|apres travaux|objectif|envisag|estim|prevision|jusqu", context):
            continue
        if 1.5 <= value <= 25:
            return value
    return None


def _first_amount(text: str, keyword: str, window: int = 70) -> float | None:
    for value, _after, _before in _amounts_after(text, re.compile(keyword), window):
        return value
    return None


def _detail_number(details: dict, *names: str) -> float | None:
    for name in names:
        value = details.get(name)
        if value is None:
            continue
        cleaned = re.sub(r"[^\d,.]", "", str(value).replace(" ", "").replace(" ", ""))
        if not cleaned:
            continue
        try:
            return float(cleaned.replace(",", ".")) if cleaned.count(",") <= 1 and cleaned.count(".") <= 1 else None
        except ValueError:
            continue
    return None


# Clés standard de `details_json`. Un connecteur dont la source publie ces
# montants dans des champs structurés les écrit sous ces noms exacts (en euros,
# montants annuels) ; ils priment alors sur l'analyse du texte.
STRUCTURED_KEYS = {
    "loyer_annuel_declare": "Loyer annuel",
    "rendement_declare": "Rentabilité brute",
    "chiffre_affaires": "Chiffre d'affaires",
    "ebe": "EBE",
    "resultat": "Résultat net",
    "taxe_fonciere": "Taxe foncière",
    "charges_copro_annuelles": "Charges annuelles de copropriété",
    "travaux_annonces": "Travaux à prévoir",
}


@dataclass(slots=True)
class Finance:
    loyer_annuel_declare: float | None = None
    rendement_declare: float | None = None
    chiffre_affaires: float | None = None
    ebe: float | None = None
    resultat: float | None = None
    taxe_fonciere: float | None = None
    charges_copro_annuelles: float | None = None
    travaux_annonces: float | None = None
    nb_lots: int | None = None
    bien_loue: bool = False
    immeuble_rapport: bool = False
    droit_au_bail: bool = False
    segment: str = "autre"


def extract(
    titre: str | None, description: str | None, details_json: str | None,
    prix: float | None, type_bien: str | None,
) -> Finance:
    try:
        details = json.loads(details_json) if details_json else {}
        if not isinstance(details, dict):
            details = {}
    except (TypeError, json.JSONDecodeError):
        details = {}
    text = _plain(" ".join(filter(None, [titre, description])))
    flat_details = _plain(" ".join(f"{key} : {value}" for key, value in details.items()))
    result = Finance()

    monthly_rent = _detail_number(details, "Loyer hors charges mensuel")
    if monthly_rent and prix:
        result.loyer_annuel_declare = monthly_rent * 12
    elif prix:
        result.loyer_annuel_declare = _rent(text, prix)

    result.rendement_declare = _percent(text, r"rentabilite|rendement|renta\b")
    if result.loyer_annuel_declare is None and result.rendement_declare and prix:
        result.loyer_annuel_declare = prix * result.rendement_declare / 100

    result.chiffre_affaires = _first_amount(
        text, r"chiffre d.affaires?(?: annuel)?(?: ht)?(?: \d{4})?|\bc\.?a\.?(?: ht)?(?: \d{4})?\s*[:=]", 60,
    )
    result.ebe = _first_amount(text, r"\bebe\b|excedent brut d.exploitation", 60)
    result.resultat = _first_amount(text, r"resultat (?:net|d.exploitation|courant)|benefice(?: net)?", 60)

    result.taxe_fonciere = _detail_number(details, "Taxe foncière") or _first_amount(
        text, r"taxe fonciere", 40,
    )
    result.charges_copro_annuelles = _detail_number(
        details, "Charges annuelles de copropriété",
        "Montant des charges prévisionnelles annuelles moyen",
    )
    if result.charges_copro_annuelles is None:
        monthly = re.search(r"charges mensuelles copro : ([\d.]+)", flat_details)
        if monthly:
            result.charges_copro_annuelles = float(monthly.group(1)) * 12
    result.travaux_annonces = _detail_number(details, "Travaux à prévoir") or _first_amount(
        text, r"travaux (?:a prevoir|estimes?|chiffres?|evalues?)(?: a)?", 50,
    )

    lots = re.search(r"(\d{1,2})\s+(?:lots|appartements|logements|studios)\b", text)
    if lots and 2 <= int(lots.group(1)) <= 60:
        result.nb_lots = int(lots.group(1))
    result.immeuble_rapport = bool(re.search(
        r"immeuble de rapport|immeuble (?:entierement |integralement )?loue|vendu en bloc|vente en bloc|"
        r"immeuble (?:de|compose de|comprenant) \d+ (?:appartements|logements|studios)|"
        r"(?:ensemble|immeuble) (?:de|compose de|comprenant) (?:plusieurs|\d+) (?:appartements|logements)",
        text,
    ))
    result.bien_loue = bool(
        re.search(
            r"vendu[es]? loue|actuellement loue|deja loue|bien loue|murs (?:loues|occupes)|"
            r"locataires? en place|(?<!aucun )(?<!pas de )(?<!sans )bail (?:en cours|commercial en cours|3.6.9)|"
            r"occupe par|investissement locatif cle en main|loyer (?:actuel|en place|percu)",
            text,
        )
        or "murs occupés" in flat_details
    ) or result.loyer_annuel_declare is not None and type_bien != "fonds_commerce"
    result.droit_au_bail = bool(re.search(r"droit au bail|cession de bail", text))
    for field_name, key in STRUCTURED_KEYS.items():
        value = _detail_number(details, key)
        if value is not None and value > 0:
            setattr(result, field_name, value)
    if "Loyer annuel" in details and type_bien != "fonds_commerce" and _detail_number(details, "Loyer annuel"):
        result.bien_loue = True
    result.segment = segment(type_bien, text, result)
    if result.segment == "fonds":
        # Dans un fonds, le loyer est une charge payée au bailleur des murs.
        result.bien_loue = False

    # Garde-fous : un loyer ou un rendement hors d'échelle est presque toujours
    # un prix de lot, un loyer potentiel ou une erreur de saisie.
    if prix and result.loyer_annuel_declare:
        ratio = result.loyer_annuel_declare / prix
        if type_bien != "fonds_commerce" and not 0.015 <= ratio <= 0.25:
            result.loyer_annuel_declare = None
    if prix and result.chiffre_affaires and not 0.05 <= prix / result.chiffre_affaires <= 20:
        result.chiffre_affaires = None
    if result.ebe and result.chiffre_affaires and result.ebe > result.chiffre_affaires:
        result.ebe = None
    if result.taxe_fonciere and not 50 <= result.taxe_fonciere <= 60_000:
        result.taxe_fonciere = None
    if result.travaux_annonces and prix and result.travaux_annonces > prix * 1.5:
        result.travaux_annonces = None
    return result


_FONDS = re.compile(
    r"fonds de commerce|cession de fonds|vente (?:du|d.un) fonds|droit au bail|pas.de.porte|"
    r"cession de bail|fonds a ceder|cede (?:son|le) fonds|licence (?:iv|4)|clientele|"
    r"chiffre d.affaires|\bebe\b|affaire a reprendre|reprise d.activite"
)
_MURS = re.compile(
    r"murs (?:commerciaux|occupes|loues|libres|d.un|de la|du)|vente des murs|"
    r"vendus? (?:avec|sans) (?:le )?fonds|investissement (?:locatif|murs)|locataire en place"
)
_FONDS_TITLE = re.compile(
    r"^vente (?:bar|restaurant|restauration|hotel|tabac|boulangerie|boucherie|coiffure|"
    r"esthetique|pressing|epicerie|fleuriste|pizzeria|camping|commerce|fonds)"
)


def segment(type_bien: str | None, text: str, finance: "Finance") -> str:
    """Stratégie d'investissement réellement applicable, indépendante du libellé source."""
    if type_bien == "fonds_commerce":
        return "fonds"
    if type_bien == "terrain":
        return "terrain"
    if type_bien in {"local_commercial", "bureau", "autre"}:
        if _FONDS.search(text) or _FONDS_TITLE.search(text):
            # « Murs et fonds » : le fonds domine la valorisation et le
            # rendement des murs n'est pas observable séparément.
            if not _MURS.search(text) or re.search(r"murs et (?:le )?fonds|fonds et (?:les )?murs", text):
                return "fonds"
        if type_bien == "autre" and (finance.immeuble_rapport or re.search(r"^vente immeuble|\bimmeuble\b", text)):
            return "immeuble"
        return "murs" if type_bien in {"local_commercial", "bureau"} else "autre"
    if type_bien in {"appartement", "maison"}:
        # « appartement dans un immeuble de 12 lots » décrit la copropriété,
        # pas une vente en bloc : seul un vocabulaire d'immeuble de rapport compte.
        if finance.immeuble_rapport and re.search(r"^(?:vente )?(?:immeuble|ensemble)|immeuble de rapport|en bloc", text):
            return "immeuble"
        return "residentiel"
    return "autre"


FINGERPRINT = f"hash({PARSER_VERSION}, a.prix, a.type_bien, a.titre, a.description, a.details_json)"


def refresh_finance(con: duckdb.DuckDBPyConnection, source: str | None = None) -> int:
    con.execute("""
        CREATE TABLE IF NOT EXISTS annonce_finance (
            source VARCHAR, external_id VARCHAR, fingerprint UBIGINT,
            loyer_annuel_declare DOUBLE, rendement_declare DOUBLE,
            chiffre_affaires DOUBLE, ebe DOUBLE, resultat DOUBLE,
            taxe_fonciere DOUBLE, charges_copro_annuelles DOUBLE,
            travaux_annonces DOUBLE, nb_lots INTEGER, bien_loue BOOLEAN,
            immeuble_rapport BOOLEAN, droit_au_bail BOOLEAN, segment VARCHAR,
            PRIMARY KEY (source, external_id)
        )
    """)
    scope = "AND a.source=?" if source else ""
    params = [source] if source else []
    rows = con.execute(f"""
        SELECT a.source, a.external_id, {FINGERPRINT}, a.titre, a.description,
               a.details_json, a.prix, a.type_bien
        FROM annonces_stg a
        LEFT JOIN annonce_finance f
          ON f.source=a.source AND f.external_id=a.external_id
        WHERE (a.categorie='vente' OR a.type_bien='fonds_commerce') {scope}
          AND (f.fingerprint IS NULL OR f.fingerprint <> {FINGERPRINT})
    """, params).fetchall()
    if not rows:
        return 0
    columns = ["source", "external_id", "fingerprint", *Finance.__slots__]
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", encoding="utf-8", delete=False) as stream:
        temp_path = stream.name
        for source_name, external_id, fingerprint, titre, description, details, prix, type_bien in rows:
            values = asdict(extract(titre, description, details, prix, type_bien))
            stream.write(json.dumps(
                {"source": source_name, "external_id": external_id, "fingerprint": fingerprint, **values},
                ensure_ascii=False,
            ) + "\n")
    try:
        # Une insertion ligne à ligne prend plus de dix minutes sur 300 000
        # annonces ; le chargement JSONL en bloc prend quelques secondes.
        escaped = temp_path.replace("'", "''")
        types = ", ".join(f"'{name}': '{kind}'" for name, kind in _COLUMN_TYPES.items())
        con.execute(f"""
            INSERT OR REPLACE INTO annonce_finance BY NAME
            SELECT * FROM read_json('{escaped}', format='newline_delimited', columns={{{types}}})
        """)
    finally:
        Path(temp_path).unlink(missing_ok=True)
    return len(rows)


_COLUMN_TYPES = {
    "source": "VARCHAR", "external_id": "VARCHAR", "fingerprint": "UBIGINT",
    "loyer_annuel_declare": "DOUBLE", "rendement_declare": "DOUBLE",
    "chiffre_affaires": "DOUBLE", "ebe": "DOUBLE", "resultat": "DOUBLE",
    "taxe_fonciere": "DOUBLE", "charges_copro_annuelles": "DOUBLE",
    "travaux_annonces": "DOUBLE", "nb_lots": "INTEGER", "bien_loue": "BOOLEAN",
    "immeuble_rapport": "BOOLEAN", "droit_au_bail": "BOOLEAN", "segment": "VARCHAR",
}
