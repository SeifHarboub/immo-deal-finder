"""Bpifrance Transmission : entreprises à reprendre et locaux à vendre.

Deux familles d'URL :
- `/annonce/{slug}-{id}` : cessions d'entreprises et de fonds -> 'fonds_commerce' ;
- `/locaux/annonce-locaux/{slug}-{hex32}` : locaux, seules les ventes sont gardées.
Seul le département est publié : `code_postal` reste NULL, le département va
dans `details_json["Département"]`. Robots : jamais `/tracking/`, `/search`, `/contact/`.
"""

from collections.abc import Iterator
from datetime import datetime
from html import unescape
import json
import os
import re

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


BASE = "https://reprise-entreprise.bpifrance.fr"
_BUSINESS = re.compile(r"/annonce/[a-z0-9-]+-(\d+)$")
_PREMISES = re.compile(r"/locaux/annonce-locaux/([a-z0-9-]+)-([0-9a-f]{32})$")
_SALE_SLUG = re.compile(r"^(?:vente-|murs|ensemble-immobilier)|(?:^|-)(?:vente|vendre|vte|ceder|cession)(?:-|$)")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTHS.update({"fév": 2, "fev": 2, "avr": 4, "mai": 5, "juin": 6, "juil": 7, "aoû": 8, "aou": 8, "déc": 12})


def _text(html: str | None) -> str:
    text = re.sub(r"<br\s*/?>|</p>", "\n", html or "", flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", " ", text)).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def amount(value: str | None) -> float | None:
    """« 7 500 k€ » -> 7 500 000 ; « 350 000 € » -> 350 000 ; NC -> None."""
    if not value:
        return None
    match = re.search(r"(\d[\d\s.,]*)\s*(k|m)?\s*€", value.replace("\xa0", " "), re.I)
    if not match:
        return None
    raw = re.sub(r"\s", "", match.group(1))
    raw = raw.replace(".", "").replace(",", ".") if re.search(r"\.\d{3}(?!\d)", raw) else raw.replace(",", ".")
    try:
        number = float(raw)
    except ValueError:
        return None
    unit = (match.group(2) or "").lower()
    number *= 1_000 if unit == "k" else 1_000_000 if unit == "m" else 1
    return number or None


def keep_url(url: str) -> bool:
    if "tracking" in url:
        return False
    path = url.replace(BASE, "")
    if _BUSINESS.search(path):
        return True
    premises = _PREMISES.search(path)
    return bool(premises and _SALE_SLUG.search(premises.group(1)))


class BpifranceConnector(AgencyJsonLdConnector):
    PARSER_VERSION = 1

    def __init__(self) -> None:
        super().__init__("bpifrance", f"{BASE}/sitemap-index.xml", keep_url)
        for key, value in (("SCRAPE_MIN_DELAY_BPIFRANCE", "0.5"),
                           ("SCRAPE_MAX_DELAY_BPIFRANCE", "1.0"),
                           ("AGENCY_FETCH_WORKERS_BPIFRANCE", "4")):
            os.environ.setdefault(key, value)

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        record = parse_detail(html, page_url.split("?", 1)[0])
        if record:
            yield record


def _stats(html: str) -> list[tuple[str, str]]:
    blocks = re.findall(
        r'<div class="annonce__card__information__statistics__item[^"]*"\s*(?:title="([^"]*)")?\s*>(.*?)</div>',
        html, re.S,
    )
    return [(unescape(title or ""), _text(body)) for title, body in blocks]


def parse_detail(html: str, url: str) -> dict | None:
    business = _BUSINESS.search(url)
    premises = _PREMISES.search(url)
    if not business and not premises:
        return None
    title = re.search(r'annonce__card__information__title">\s*<h1[^>]*>(.*?)</h1>', html, re.S)
    if not title:
        return None
    name = re.sub(r"^\s*[àa] vendre\s*:\s*", "", _text(title.group(1)), flags=re.I)
    desc = re.search(r'annonce__card__information__description">(.*?)</div>', html, re.S)
    body = _text(desc.group(1)) if desc else None
    published = None
    date = re.search(r"Publié le\s+(\d{1,2})\s+([A-Za-zéû]+)\.?\s+(\d{4})", html)
    if date:
        word = date.group(2).lower()
        month = _MONTHS.get(word[:4]) or _MONTHS.get(word[:3])
        if month:
            published = datetime(int(date.group(3)), month, int(date.group(1))).date().isoformat()
    details: dict = {}
    price = surface = None
    transaction = ""
    for label, value in _stats(html):
        if label == "Secteur d'activité":
            details["Secteur" if business else "Catégorie"] = value
        elif value.startswith("Département"):
            dept = re.search(r"Département\s*:\s*(\S+)\s*(.*)", value)
            if dept and dept.group(1) != "NC":
                details["Département"] = dept.group(1)
            region = re.search(r"\(([^)]+)\)\s*$", value)
            if region:
                details["Région"] = region.group(1)
            if dept and dept.group(2) and not dept.group(2).startswith("("):
                details["Localisation"] = dept.group(2).strip()
        elif label == "Effectifs":
            staff = re.search(r"(\d[\d\s]*)", value)
            details["Effectif"] = int(re.sub(r"\s", "", staff.group(1))) if staff else value
        elif label == "Chiffre d'affaires" and value.startswith("CA"):
            details["CA affiché"] = value
        elif label in ("Prix de cession", "Prix de vente"):
            price = amount(value)
        elif label == "Surface":
            match = re.search(r"([\d\s.,]+)\s*m", value)
            surface = float(re.sub(r"\s", "", match.group(1)).replace(",", ".")) if match else None
        elif not label:
            transaction = value
    if premises and transaction and not transaction.lower().startswith("vente"):
        return None  # Location ou autre : hors périmètre pour ce site.
    for key, raw in re.findall(
        r'list__item__title">\s*([^<]+?)\s*</span>\s*<span class="annonce__detail__list__item__value">(.*?)</span>',
        html, re.S,
    ):
        value = amount(_text(raw))
        if not value:
            continue
        key = key.strip().upper()
        if key == "CA":
            details["Chiffre d'affaires"] = value
        elif key == "EBE":
            details["EBE"] = value
        elif key in ("RN", "RÉSULTAT NET", "RESULTAT NET"):
            details["Résultat net"] = value
    ca = details.get("Chiffre d'affaires")
    if ca:
        # Contrôle d'échelle : quelques partenaires saisissent des euros dans un champ en k€.
        staff = details.get("Effectif") if isinstance(details.get("Effectif"), int) else None
        if ca >= 1e9 or (staff is not None and staff <= 5 and ca > 1e8):
            details["Chiffre d'affaires"] = ca / 1_000
            if details.get("EBE"):
                details["EBE"] = details["EBE"] / 1_000
            details["Échelle corrigée"] = "k€ -> €"
    partner = re.search(r'data-partner="([^"]+)"', html)
    if partner:
        details["Partenaire"] = partner.group(1)
    if premises:
        kind = transaction.lower()
        type_bien = ("bureau" if "bureau" in kind else "terrain" if "terrain" in kind
                     else "autre" if re.search(r"entrep|activit|industri|atelier", kind)
                     else "immeuble" if "immeuble" in kind else "local_commercial")
        if transaction:
            details["Type de bien"] = transaction
        external_id = premises.group(2)
    else:
        type_bien = "fonds_commerce"
        external_id = business.group(1)
    details = {key: value for key, value in details.items() if value not in (None, "")}
    return {
        "id": external_id, "name": name, "price": price, "surface": surface,
        "land_surface": None, "rooms": None, "zipcode": None, "city": None,
        "lat": None, "lng": None, "type_hint": f"vente {type_bien}", "url": url,
        "body": body, "seller_name": details.get("Partenaire"), "image_count": 0,
        "published_at": published,
        "details_json": json.dumps(details, ensure_ascii=False),
        "images_json": "[]", "reference_annonce": external_id,
        "raw_payload": json.dumps({"stats": _stats(html)}, ensure_ascii=False),
    }


register(BpifranceConnector())
