"""CBRE France : offres à la vente (`/offre/a-vendre/...`) d'immobilier d'entreprise.

JSON-LD `SellAction` : prix, identifiant, code postal, commune et catégorie
(« Vente / Locaux commerciaux / CESSY »). Le prix publié peut être un prix au m²
(« A partir de 2 300 € m² HT ») : il est alors rangé dans les détails, pas en prix.
"""

from collections.abc import Iterator
from html import unescape
import json
import os
import re

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


_URL = re.compile(r"/offre/a-vendre/([a-z-]+)/(\d{5}|[0-9ab]{5})/[^/]+/(\d+)\.aspx$", re.I)
TYPES = {"bureaux": "bureau", "commerces": "local_commercial", "activites": "autre",
         "entrepots": "autre", "terrains": "terrain", "coworking": "bureau"}


def _flat(html: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
    text = re.sub(r"<[^>]+>", "|", text)
    text = unescape(text).replace("\xa0", " ").replace(" ", " ")
    return re.sub(r"(\|\s*)+", "|", re.sub(r"\s+", " ", text))


def _num(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"\d[\d\s.]*(?:,\d+)?", str(value))
    if not match:
        return None
    try:
        return float(re.sub(r"[\s.]", "", match.group(0)).replace(",", ".")) or None
    except ValueError:
        return None


class CbreConnector(AgencyJsonLdConnector):
    PARSER_VERSION = 1

    def __init__(self) -> None:
        super().__init__("cbre", "https://immobilier.cbre.fr/sitemap.xml",
                         lambda url: "/offre/a-vendre/" in url and bool(_URL.search(url)))
        for key, value in (("SCRAPE_MIN_DELAY_CBRE", "0.5"), ("SCRAPE_MAX_DELAY_CBRE", "1.0"),
                           ("AGENCY_FETCH_WORKERS_CBRE", "4")):
            os.environ.setdefault(key, value)

    def _map_node(self, node: dict, page_url: str) -> dict | None:
        return node if node.get("@type") == "SellAction" else None

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        node = next(super().parse_page(html, page_url), None)
        if node:
            record = map_offer(node, html, page_url)
            if record:
                yield record


def map_offer(node: dict, html: str, page_url: str) -> dict | None:
    url = (node.get("url") or page_url).split("?", 1)[0]
    match = _URL.search(url) or _URL.search(page_url)
    if not match:
        return None
    kind, postal, numeric_id = match.groups()
    location = node.get("location") or {}
    product = node.get("object") or {}
    flat = _flat(html)
    displayed = re.search(r"\|Prix(?: de vente)?\|: \|([^|]+)\|", flat)
    displayed_price = displayed.group(1).strip() if displayed else None
    updated = re.search(r"Annonce mise à jour le (\d{2})/(\d{2})/(\d{4})", flat)
    details: dict = {"Catégorie": product.get("category"), "Prix affiché": displayed_price,
                     "Mise à jour": "-".join(reversed(updated.groups())) if updated else None}
    price = _num(node.get("price") or (product.get("offers") or {}).get("price"))
    if displayed_price and re.search(r"m²|m2|/an|mois|loyer", displayed_price, re.I):
        details["Prix au m²"] = _num(displayed_price) if "m" in displayed_price else None
        price = None
    elif displayed_price and not re.search(r"\d", displayed_price):
        price = None  # « Nous consulter »
    surface = None
    after_price = re.search(r"\|Prix(?: de vente)?\|: \|[^|]+\|([\d\s.,]+) m²\|", flat)
    if after_price:
        surface = _num(after_price.group(1))
    if surface is None:
        found = re.search(r"-\s*([\d\s.,]+)\s*m²", node.get("description") or "")
        surface = _num(found.group(1)) if found else None
    divisible = re.search(r"\|(Non divisible|Divisibilité min\.[^|]+)\|", flat)
    if divisible:
        details["Divisibilité"] = divisible.group(1)
    body_match = re.search(r"\|Description\|Annonce mise à jour le [^|]+\|(.*?)\|(?:Aménagements|Surfaces|Localisation)\|", flat)
    body = body_match.group(1).replace("|", "\n").strip() if body_match else node.get("description")
    category = (product.get("category") or "").lower()
    type_bien = TYPES.get(kind.lower(), "autre")
    if "commerciaux" in category:
        type_bien = "local_commercial"
    agent = node.get("agent") or {}
    image = node.get("image")
    images = [image] if isinstance(image, str) else []
    details = {key: value for key, value in details.items() if value not in (None, "")}
    return {
        "id": numeric_id, "name": node.get("name"), "price": price, "surface": surface,
        "land_surface": None, "rooms": None,
        "zipcode": location.get("postalcode") or location.get("postalCode") or postal,
        "city": (location.get("addressLocality") or "").title() or None,
        "lat": None, "lng": None, "type_hint": f"vente {type_bien}", "url": url, "body": body,
        "seller_name": (agent.get("employee") or {}).get("familyName") or agent.get("name"),
        "image_count": len(images),
        "published_at": None,
        "details_json": json.dumps(details, ensure_ascii=False),
        "images_json": json.dumps(images, ensure_ascii=False),
        "reference_annonce": node.get("identifier"),
        "raw_payload": json.dumps(node, ensure_ascii=False, default=str),
    }


register(CbreConnector())
