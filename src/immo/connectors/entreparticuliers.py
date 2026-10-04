from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from html import unescape
import json
import os
import re
import time
from xml.etree import ElementTree

import requests

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


TYPES = {"maison": "maison", "appartement": "appartement", "immeuble": "immeuble", "terrain": "terrain"}
SITEMAP = "https://www.entreparticuliers.com/sitemap-annonces-{}.xml"


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).replace("\xa0", " ").strip()


def _number(value) -> float | None:
    match = re.search(r"\d[\d\s .]*(?:,\d+)?", str(value or ""))
    if not match:
        return None
    return float(re.sub(r"[\s .]", "", match.group(0)).replace(",", "."))


def _max_age() -> timedelta:
    return timedelta(days=int(os.getenv("ENTREPARTICULIERS_MAX_AGE_DAYS", "365")))


class EntreParticuliersConnector(AgencyJsonLdConnector):
    """EntreParticuliers : annonces de particuliers à particuliers (vente uniquement)."""

    def __init__(self) -> None:
        super().__init__(
            "entreparticuliers", SITEMAP.format(1),
            lambda url: "/vente/" in url and "/annonces-immobilieres/" in url,
        )

    def _sitemap_urls(self, url: str, seen=None) -> Iterator[str]:
        """Parcourt les sitemaps du plus récent au plus ancien et écarte via
        `lastmod` les annonces non modifiées depuis plus d'un an."""
        count = int(os.getenv("ENTREPARTICULIERS_SITEMAPS", "5"))
        oldest = (datetime.now(timezone.utc) - _max_age()).date().isoformat()
        for index in range(count, 0, -1):
            response = None
            for attempt in range(3):
                try:
                    response = self.session.get(SITEMAP.format(index), timeout=60)
                    if response.status_code == 404:
                        response = None
                    break
                except requests.RequestException:
                    time.sleep(2 * (attempt + 1))
            if response is None or not response.ok:
                continue
            try:
                root = ElementTree.fromstring(response.content)
            except ElementTree.ParseError:
                continue
            entries = []
            for node in root:
                loc = next((child.text for child in node if child.tag.endswith("loc")), None)
                lastmod = next((child.text for child in node if child.tag.endswith("lastmod")), None) or ""
                if loc and self.url_filter(loc.strip()) and (not lastmod or lastmod[:10] >= oldest):
                    entries.append((lastmod, loc.strip()))
            entries.sort(reverse=True)
            yield from (loc for _lastmod, loc in entries)

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        listing = None
        for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
            try:
                node = json.loads(block)
            except json.JSONDecodeError:
                continue
            if isinstance(node, dict) and node.get("@type") == "RealEstateListing":
                listing = node
                break
        kind = re.search(r"/annonces-immobilieres/([a-z-]+)/vente/", page_url)
        type_hint = TYPES.get(kind.group(1)) if kind else None
        if not listing or not type_hint:
            return
        posted = listing.get("datePosted")
        try:
            posted_at = datetime.fromisoformat(posted) if posted else None
        except ValueError:
            posted_at = None
        if posted_at and posted_at < datetime.now(posted_at.tzinfo or timezone.utc) - _max_age():
            return
        features = {
            _text(label).lower(): _text(value) for label, value in re.findall(
                r'<div class="text-xs text-ink-400 uppercase tracking-wide">(.*?)</div>\s*'
                r'<div class="font-bold text-ink-900">(.*?)</div>', html, re.S,
            )
        }
        badges = [_text(badge) for badge in re.findall(r'<span class="inline-flex items-center px-3 py-1 rounded-lg[^"]*">(.*?)</span>', html, re.S)]
        if "Location" in badges:
            return
        private = "Particulier" in badges
        address = listing.get("address") or {}
        floor_size = listing.get("floorSize") or {}
        description = listing.get("description") or ""
        description = re.sub(r"&nbsp;", " ", description)
        description = "\n".join(
            line.strip() for line in unescape(re.sub(r"<br\s*/?>", "\n", description)).splitlines() if line.strip()
        ) or None
        images: list[str] = []
        gallery = re.search(r'<script type="application/json" data-photo-carousel-target="data">(.*?)</script>', html, re.S)
        if gallery:
            try:
                paths = json.loads(gallery.group(1))
            except json.JSONDecodeError:
                paths = []
            for path in paths:
                media = re.search(r"(/media/[^\s\"']+)$", str(path))
                if media:
                    images.append("https://www.entreparticuliers.com" + media.group(1))
        details: dict = {"Type d'annonceur": "Particulier" if private else "Professionnel"}
        for label, value in features.items():
            if label in {"type", "pièces", "surface", "terrain", "chambres"}:
                continue
            details[label.capitalize()] = value
        if re.search(r"\bviager\b", f"{listing.get('name')} {description}", re.I):
            details["Viager"] = "oui"
        equipments = [
            _text(item) for item in re.findall(
                r'<div class="flex items-center gap-3 text-\[15px\][^"]*">.*?</span>(.*?)</div>', html, re.S)
        ]
        if equipments:
            details["Équipements"] = [item for item in equipments if item]
        energy = re.search(r"Consommation \(DPE\).*?Classe ([A-G]) ·\s*<span[^>]*>([^<]+)</span>", html, re.S)
        carbon = re.search(r"Émissions \(GES\).*?Classe ([A-G]) ·\s*<span[^>]*>([^<]+)</span>", html, re.S)
        if energy:
            details["DPE consommation (tranche)"] = _text(energy.group(2))
        if carbon:
            details["GES émissions (tranche)"] = _text(carbon.group(2))
        reference = re.search(r"/ref-(\d+)", page_url)
        reference = reference.group(1) if reference else None
        details["Référence"] = reference
        details = {key: value for key, value in details.items() if value not in (None, "", [], {})}
        dpe = energy.group(1) if energy else ""
        ges = carbon.group(1) if carbon else ""
        yield {
            "id": reference or page_url, "name": listing.get("name"), "price": listing.get("price"),
            "surface": floor_size.get("value") if isinstance(floor_size, dict) else floor_size,
            "land_surface": _number(features.get("terrain")),
            "rooms": listing.get("numberOfRooms") or _number(features.get("pièces")),
            "bedrooms": _number(features.get("chambres")),
            "zipcode": address.get("postalCode"), "city": address.get("addressLocality"),
            "lat": None, "lng": None, "type_hint": type_hint, "url": page_url, "body": description,
            "dpe": dpe if dpe in tuple("ABCDEFG") else None,
            "ges": ges if ges in tuple("ABCDEFG") else None,
            "seller_name": "Particulier" if private else None,
            "seller_type": "private" if private else "pro",
            "published_at": posted,
            "details_json": json.dumps(details, ensure_ascii=False),
            "images_json": json.dumps(images, ensure_ascii=False),
            "image_count": len(images), "reference_annonce": reference,
            "raw_payload": json.dumps({"listing": listing, "features": features}, ensure_ascii=False, default=str),
        }


register(EntreParticuliersConnector())
