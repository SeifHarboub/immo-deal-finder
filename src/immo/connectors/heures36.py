"""36heures.immo : ventes interactives (offres en ligne sur une fenêtre limitée).

prix = première offre possible ; date_vente = fin des offres. Le résultat final
n'est pas publié : prix_adjuge reste vide. /api/ est interdit par robots.txt.
"""

from collections.abc import Iterator
import json
import re

from immo.connectors.base import register
from immo.connectors.licitor import (
    EnchereConnector, clean_text, make_record, occupation, parse_amount,
    parse_fr_date, type_from_text,
)


BASE = "https://www.36heures.immo"


def _first(pattern: str, text: str, flags=re.S | re.I) -> str | None:
    match = re.search(pattern, text or "", flags)
    return match.group(1) if match else None


def _graph(html: str) -> list[dict]:
    nodes = []
    for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            value = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in (value.get("@graph", [value]) if isinstance(value, dict) else value):
            if isinstance(node, dict):
                nodes.append(node)
    return nodes


def _energy(html: str, anchor: str) -> str | None:
    position = html.find(anchor)
    if position < 0:
        return None
    return _first(r'ring-2[^"]*"[^>]*>\s*([A-G])\s*<', html[position:position + 6000])


class Heures36Connector(EnchereConnector):
    PARSER_VERSION = 1
    MODE_VENTE = "vente_interactive"

    def __init__(self) -> None:
        super().__init__("heures36", BASE + "/sitemaps/sitemap-biens-1.xml",
                         lambda url: "/fr/annonce/" in url)

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        nodes = _graph(html)
        listing = next((node for node in nodes if node.get("@type") == "RealEstateListing"), None)
        identifier = _first(r"/annonce/([0-9A-Z]{26})/", page_url)
        if not listing or not identifier:
            return
        offers = listing.get("offers") or {}
        address = listing.get("address") or {}
        floor = listing.get("floorSize") or {}
        crumbs = next((node.get("itemListElement") for node in nodes if node.get("@type") == "BreadcrumbList"), [])
        crumb_type = crumbs[3].get("name") if isinstance(crumbs, list) and len(crumbs) > 3 else None
        text = clean_text(re.sub(r"<(script|style|svg)\b.*?</\1>", " ", html, flags=re.S))
        flat = re.sub(r"\s+", " ", text)
        body = _first(r"Description du bien\s*\n(.*?)(?:\n\s*Diagnostics immobiliers|\n\s*Emplacement|$)", text)
        subtitle = _first(r"Vente interactive · [^\n]+\n\s*([^\n]+)", text)
        start = parse_fr_date(_first(r"Début des offres\s+(\d{2}/\d{2}/\d{4}\s+\d{1,2}\s*h\s*\d{2})", flat))
        end = parse_fr_date(_first(r"Fin des offres\s+(\d{2}/\d{2}/\d{4}\s+\d{1,2}\s*h\s*\d{2})", flat))
        state = _first(r"(Vente à venir|Vente en cours|Vente terminée|Vendu|Offres terminées)", flat)
        statut = "à venir"
        if state and re.search(r"termin|Vendu", state):
            statut = "adjugé" if "Vendu" in state else "non communiqué"
        land = parse_amount(_first(r"([\d\s]+)\s*m²\s*-\s*Surface (?:parcelle|terrain)", flat))
        kind = type_from_text(crumb_type or subtitle or listing.get("name") or page_url)
        surface = parse_amount(floor.get("value") if isinstance(floor, dict) else floor)
        images = [image for image in listing.get("image") or [] if isinstance(image, str)]
        details = {
            "Statut": statut, "Mise à prix": parse_amount(offers.get("price")),
            "Début des offres": start, "Fin des offres": end, "État affiché": state,
            "Occupation": occupation(body), "Type détaillé": subtitle,
            "Pas d'enchère": parse_amount(_first(r"Palier d'enchère minimum de ([\d\s]+)\s*€", flat)),
            "Honoraires": _first(r"(Honoraires à la charge (?:du|de l'|des) \w+)", flat),
            "Prochaine offre possible": parse_amount(_first(r"Prochaine offre possible\s*:\s*([\d\s]+)\s*€", flat)),
            "Référence": _first(r"Référence du bien\s*:\s*([^\n]+)", text),
        }
        seller = (listing.get("seller") or listing.get("provider") or {}).get("name")
        yield make_record(
            id=identifier, name=(body or "").split("\n", 1)[0][:200] or listing.get("name"),
            price=offers.get("price"), surface=None if kind == "terrain" else surface,
            land_surface=(land or surface) if kind == "terrain" else None if kind == "appartement" else land,
            rooms=listing.get("numberOfRooms"), bedrooms=listing.get("numberOfBedrooms"),
            zipcode=address.get("postalCode"), city=address.get("addressLocality"),
            type_hint=kind, url=page_url, body=body, seller_name=seller,
            dpe=_energy(html, "Diagnostic de performance"), ges=_energy(html, "Indice d&#039;émission"),
            published_at=listing.get("datePosted"), reference_annonce=identifier,
            mode_vente=self.MODE_VENTE, date_vente=end, details=details, images=images,
            raw_payload=json.dumps({k: v for k, v in listing.items() if k != "image"}, ensure_ascii=False),
        )


register(Heures36Connector())
