"""Arthur Loyd : ventes d'immobilier d'entreprise et cessions de fonds/droits au bail.

Sitemap `sitemap-offer.xml` ; seules les rubriques de vente/cession sont gardées.
Type, région, département et ville viennent du chemin
`/{rubrique}/{region}/{dept}/{ville}/{slug}-ref-{REF}` ; prix via JSON-LD
`Product.offers.lowPrice`, coordonnées via la carte de la fiche.
"""

from collections.abc import Iterator
from html import unescape
import json
import os
import re

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


BASE = "https://www.arthur-loyd.com"
_URL = re.compile(
    r"^https://www\.arthur-loyd\.com/([a-z-]+-(?:vente|cession))/((?:[a-z0-9-]+/)+)([^/]+)-ref-([A-Za-z0-9._-]+)$"
)
TYPES = {
    "bureau-vente": "bureau", "locaux-activite-entrepots-vente": "autre", "locaux-commerciaux-vente": "local_commercial",
    "terrain-vente": "terrain", "logistique-vente": "autre", "fonds-de-commerce-cession": "fonds_commerce",
    "fonds-de-commerce-vente": "fonds_commerce", "locaux-commerciaux-cession": "fonds_commerce",
    "locaux-activite-entrepots-cession": "fonds_commerce",
}


def _text(html: str | None) -> str:
    text = re.sub(r"<br\s*/?>|</p>", "\n", html or "", flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", " ", text)).replace("\xa0", " ").replace(" ", " ")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def _num(value) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value) or None
    match = re.search(r"\d[\d\s.]*(?:,\d+)?", str(value))
    try:
        return float(re.sub(r"[\s.]", "", match.group(0)).replace(",", ".")) or None if match else None
    except ValueError:
        return None


def keep_url(url: str) -> bool:
    match = _URL.match(url)
    return bool(match and match.group(1) in TYPES)


class ArthurLoydConnector(AgencyJsonLdConnector):
    PARSER_VERSION = 1

    def __init__(self) -> None:
        super().__init__("arthurloyd", f"{BASE}/sitemap-offer.xml", keep_url)
        for key, value in (("SCRAPE_MIN_DELAY_ARTHURLOYD", "0.5"), ("SCRAPE_MAX_DELAY_ARTHURLOYD", "1.0"),
                           ("AGENCY_FETCH_WORKERS_ARTHURLOYD", "4")):
            os.environ.setdefault(key, value)

    def _map_node(self, node: dict, page_url: str) -> dict | None:
        return node if node.get("@type") == "Product" else None

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        node = next(super().parse_page(html, page_url), None)
        record = parse_detail(html, page_url.split("?", 1)[0], node or {})
        if record:
            yield record


def parse_detail(html: str, url: str, node: dict) -> dict | None:
    canon = re.search(r'<link rel="canonical" href="([^"]+)"', html)
    if canon and _URL.match(canon.group(1)) and not _URL.match(url):
        url = canon.group(1)
    match = _URL.match(url)
    title = re.search(r'<h1 class="offer-header__title">(.*?)</h1>', html, re.S)
    if not match or (not title and not node):
        return None
    rubric, path, _slug, ref = match.groups()
    segments = path.strip("/").split("/")  # région / [département] / [zone] / ville
    region = segments[0]
    dept = segments[1] if len(segments) >= 3 else None
    if rubric not in TYPES:
        return None
    name = _text(title.group(1)) if title else unescape(node.get("name") or "").strip()
    address = re.search(r'<span class="offer-header__address">(.*?)<a', html, re.S)
    address_text = _text(address.group(1)).strip(" –-") if address else ""
    postal = re.search(r"\b(\d{5})\b", address_text)
    city = address_text[:postal.start()].strip(" –-") if postal else address_text or None
    offers = node.get("offers") or {}
    price = _num(offers.get("lowPrice") or offers.get("price"))
    header_price = re.search(r'<span class="price">(.*?)</span>\s*<span class="price-by">(.*?)</span>', html, re.S)
    details: dict = {"Rubrique": rubric, "Région": region, "Département": dept, "Référence": ref}
    if header_price:
        unit = _text(header_price.group(2))
        details["Prix affiché"] = f"{_text(header_price.group(1))} {unit}".strip()
        if re.search(r"m²|m2|/an|mois", unit):
            details["Prix au m²"] = _num(header_price.group(1))
            price = None
    if offers.get("highPrice") and offers.get("highPrice") != offers.get("lowPrice"):
        details["Prix maximum"] = _num(offers.get("highPrice"))
    surface_text = re.search(r'offer-header__infos-surface">\s*<span>(.*?)</span>', html, re.S)
    surface = None
    if surface_text:
        values = [_num(value) for value in re.findall(r"[\d\s.,]+", _text(surface_text.group(1))) if _num(value)]
        surface = max(values) if values else None
        details["Surface affichée"] = _text(surface_text.group(1))
    if price and surface and price < 10_000 and price < surface * 50:
        # « 120 € HD » sur 36 000 m² : prix unitaire, pas un prix total.
        details["Prix au m²"] = price
        price = None
    description = re.search(r'<div id="description">\s*<p>(.*?)</p>', html, re.S)
    services = re.search(r'<div id="prestations">\s*<ul>(.*?)</ul>', html, re.S)
    if services:
        items = [_text(item) for item in re.findall(r"<li[^>]*>(.*?)</li>", services.group(1), re.S)]
        if items:
            details["Prestations"] = ", ".join(item for item in items if item)
    available = re.search(r'offer-header__infos-sep">\s*-\s*</span>\s*<div[^>]*>\s*(?:<span>)?\s*(Disponibilité[^<]+)', html)
    if available:
        details["Disponibilité"] = available.group(1).strip()
    coords = re.search(r"latitude&quot;:(-?\d+\.\d+),&quot;longitude&quot;:(-?\d+\.\d+)", html)
    images = list(dict.fromkeys(
        BASE + path for path in re.findall(r'data-background="(/media/cache/[^"]+)"', html)
    ))
    advisor = re.search(r'<span class="advisor-name">\s*([^<]+)', html)
    details = {key: value for key, value in details.items() if value not in (None, "")}
    return {
        "id": ref, "name": name, "price": price, "surface": surface, "land_surface": None, "rooms": None,
        "zipcode": postal.group(1) if postal else None, "city": city,
        "lat": float(coords.group(1)) if coords else None, "lng": float(coords.group(2)) if coords else None,
        "type_hint": f"vente {TYPES[rubric]}", "url": url,
        "body": _text(description.group(1)) if description else None,
        "seller_name": advisor.group(1).strip() if advisor else "Arthur Loyd",
        "image_count": len(images), "published_at": None,
        "details_json": json.dumps(details, ensure_ascii=False),
        "images_json": json.dumps(images, ensure_ascii=False),
        "reference_annonce": ref,
        "raw_payload": json.dumps(node, ensure_ascii=False, default=str),
    }


register(ArthurLoydConnector())
