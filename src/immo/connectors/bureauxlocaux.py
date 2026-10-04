"""BureauxLocaux : immobilier d'entreprise (ventes de tous types, locations de commerces).

Les fiches embarquent l'annonce complète dans `<script id="react-context">`.
Robots : seules les fiches `/annonce/...` sont lues ; jamais `/listings/*`
ni `/log-listing-view*`.
"""

from collections.abc import Iterator
import gzip
from html import unescape
import json
import os
import re
import time
from xml.etree import ElementTree

import requests

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


SITEMAP = "https://www.bureauxlocaux.com/sitemap.xml"
_REACT = re.compile(r'<script[^>]*id="react-context"[^>]*>(.*?)</script>', re.S)
# Locations sans intérêt (bureaux, entrepôts) repérables dès le slug : inutile
# de les télécharger puisque seules les locations de commerces sont gardées.
_SKIP_RENTAL_SLUG = re.compile(
    r"(?=.*(?:louer|location))(?=.*(?:bureau|entrepot|activite|logisti|stockage|coworking|atelier))"
    r"(?!.*(?:commerc|boutique|magasin|restaurant|murs|vente|vendre))"
)
TYPES = {
    "commercial": "local_commercial", "office": "bureau", "coworking": "bureau",
    "activity": "autre", "warehouse": "autre", "land": "terrain", "building": "immeuble",
}


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _text(html: str | None) -> str:
    text = re.sub(r"<br\s*/?>|</p>", "\n", html or "", flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", " ", text)).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def listing_id(url: str) -> str | None:
    match = re.search(r"--(\d+)$", url.split("?", 1)[0].rstrip("/"))
    return match.group(1) if match else None


class BureauxLocauxConnector(AgencyJsonLdConnector):
    PARSER_VERSION = 1

    def __init__(self) -> None:
        super().__init__("bureauxlocaux", SITEMAP, self._keep_url)
        for key, value in (("SCRAPE_MIN_DELAY_BUREAUXLOCAUX", "0.5"),
                           ("SCRAPE_MAX_DELAY_BUREAUXLOCAUX", "1.0"),
                           ("AGENCY_FETCH_WORKERS_BUREAUXLOCAUX", "4")):
            os.environ.setdefault(key, value)

    @staticmethod
    def _keep_url(url: str) -> bool:
        if "/annonce/" not in url or not listing_id(url) or url.endswith("/pdf"):
            return False
        slug = url.rsplit("/", 1)[-1].lower()
        return not _SKIP_RENTAL_SLUG.search(slug)

    def _xml(self, url: str) -> bytes | None:
        for attempt in range(3):
            try:
                response = self.session.get(url, timeout=60)
                if response.status_code == 404:
                    return None
                if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                    time.sleep(3 * (attempt + 1))
                    continue
                response.raise_for_status()
                content = response.content
                # S3 sert parfois le .gz avec Content-Encoding : déjà décompressé.
                return gzip.decompress(content) if content[:2] == b"\x1f\x8b" else content
            except (requests.RequestException, OSError):
                if attempt == 2:
                    return None
                time.sleep(3 * (attempt + 1))
        return None

    def _sitemap_urls(self, url: str, seen=None) -> Iterator[str]:
        """Index -> sitemaps `listings-N` uniquement, annonces triées par lastmod décroissant."""
        index = self._xml(url)
        if not index:
            return
        try:
            root = ElementTree.fromstring(index)
        except ElementTree.ParseError:
            return
        children = [node.text.strip() for node in root.iter() if node.tag.endswith("loc") and node.text]
        entries: list[tuple[str, str]] = []
        for child in children:
            if not re.search(r"/sitemap-listings-\d+\.xml", child):
                continue
            content = self._xml(child)
            if not content:
                continue
            try:
                tree = ElementTree.fromstring(content)
            except ElementTree.ParseError:
                continue
            for node in tree:
                loc = lastmod = None
                for sub in node:
                    if sub.tag.endswith("}loc"):
                        loc = (sub.text or "").strip()
                    elif sub.tag.endswith("}lastmod"):
                        lastmod = (sub.text or "").strip()
                if loc and self.url_filter(loc):
                    entries.append((lastmod or "", loc))
            self._pause_short()
        entries.sort(reverse=True)
        yield from (loc for _lastmod, loc in entries)

    def _pause_short(self) -> None:
        time.sleep(float(os.getenv("SCRAPE_MIN_DELAY_BUREAUXLOCAUX", "0.5")))

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        match = _REACT.search(html)
        if not match:
            return
        listing = (json.loads(match.group(1)).get("global") or {}).get("listing") or {}
        if not listing.get("id"):
            return
        record = map_listing(listing, page_url)
        if record:
            yield record


def map_listing(listing: dict, page_url: str) -> dict | None:
    chars = listing.get("characteristics_json") or {}
    realty = listing.get("realty_type") or ""
    label = listing.get("label") or ""
    body = _text(listing.get("description"))
    is_rental = bool(listing.get("is_rental")) and not listing.get("is_sale")
    type_bien = TYPES.get(realty, "autre")
    lower = f"{label} {body[:400]}".lower()
    if realty == "commercial" and not is_rental and (
        chars.get("is_sale_of_business_assets") or re.search(r"fonds de commerce|droit au bail|cession de bail", lower)
    ) and not re.search(r"\bmurs\b", label.lower()):
        type_bien = "fonds_commerce"
    if is_rental and type_bien != "local_commercial":
        return None  # Politique : seules les locations de commerces alimentent le modèle de loyers.
    monthly = _num(listing.get("monthly_rent"))
    sale_price = None if listing.get("hide_price") else _num(listing.get("sale_price"))
    surface = _num(listing.get("total_surface"))
    details: dict = {
        "Type BureauxLocaux": listing.get("realty_type_display") or realty,
        "Transaction": listing.get("transaction_type_display"),
        "Disponibilité": listing.get("availability") or None,
        "Baux": ", ".join(listing.get("full_leases_display") or []) or None,
        "Licence": None if chars.get("licence_type") in (None, "", "none") else chars.get("licence_type"),
        "Surface de vente": _num(chars.get("selling_surface")),
        "Recommandé investissement": "oui" if chars.get("is_recommended_for_investment") else None,
        "Vente de fonds": "oui" if chars.get("is_sale_of_business_assets") else None,
        "Taxe foncière": _num(listing.get("property_tax")),
        "Charges locatives": _num(listing.get("rental_costs")),
        "Charges annuelles de copropriété": _num(listing.get("joint_ownership_costs")),
        "Prix du droit au bail": _num(listing.get("leasing_right_price")),
    }
    # `yearly_rent` est un loyer au m²/an, `monthly_rent` un loyer total mensuel.
    if is_rental:
        details["Loyer HT HC €/m²/an"] = _num(listing.get("yearly_rent"))
        details["Loyer mensuel"] = monthly
    elif monthly:
        if type_bien != "fonds_commerce" and chars.get("is_occupied"):
            details["Loyer annuel"] = round(monthly * 12, 2)
        else:
            # Sens incertain (loyer payé par un repreneur, double vente/location).
            details["Loyer mensuel affiché"] = monthly
    if type_bien != "fonds_commerce" and not is_rental and chars.get("is_occupied"):
        details["Occupé"] = "oui"  # `False` n'est pas fiable : jamais traduit en « non ».
    details = {key: value for key, value in details.items() if value not in (None, "", [])}
    images = [
        (image.get("urls") or {}).get("large") or (image.get("urls") or {}).get("normal")
        for image in listing.get("images") or [] if image.get("type", "photo") == "photo"
    ]
    images = [image for image in images if image]
    contacts = listing.get("contacts_to_display") or []
    seller = None
    if contacts and isinstance(contacts[0], dict):
        seller = contacts[0].get("customer_trade_name")
    if not seller:
        customers = listing.get("customers") or []
        seller = customers[0].get("trade_name") if customers and isinstance(customers[0], dict) else None
    lettre = lambda value: value if isinstance(value, str) and re.fullmatch(r"[A-G]", value or "") else None
    external_id = str(listing["id"])
    return {
        "id": external_id, "name": label,
        "price": monthly if is_rental else sale_price,
        "surface": surface, "land_surface": None, "rooms": None,
        "zipcode": listing.get("zip_code") or None, "city": listing.get("city") or None,
        "lat": listing.get("latitude"), "lng": listing.get("longitude"),
        "type_hint": f"{'location' if is_rental else 'vente'} {type_bien}",
        "url": page_url.split("?", 1)[0], "body": body, "seller_name": seller,
        "dpe": lettre(listing.get("energy_assessment")), "ges": lettre(listing.get("greenhouse_gas")),
        "image_count": len(images), "published_at": None,
        "details_json": json.dumps(details, ensure_ascii=False),
        "images_json": json.dumps(images, ensure_ascii=False),
        "reference_annonce": listing.get("reference") or None,
        "raw_payload": json.dumps({
            key: listing.get(key) for key in (
                "id", "label", "is_sale", "is_rental", "realty_type", "sale_price", "hide_price",
                "total_surface", "yearly_rent", "monthly_rent", "zip_code", "city", "reference",
                "availability", "property_tax", "price_display",
            )
        } | {"characteristics_json": chars}, ensure_ascii=False, default=str),
    }


register(BureauxLocauxConnector())
