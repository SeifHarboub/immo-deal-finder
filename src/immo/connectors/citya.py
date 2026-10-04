from collections.abc import Iterator
from html import unescape
import json
import re

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


TYPES = {"maison": "maison", "appartement": "appartement", "immeuble": "immeuble"}


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).replace("\xa0", " ").strip()


def _euros(text: str) -> float | None:
    match = re.search(r"(\d[\d\s ]*(?:[.,]\d+)?)\s*€", text)
    return float(re.sub(r"[\s ]", "", match.group(1)).replace(",", ".")) if match else None


class CityaConnector(AgencyJsonLdConnector):
    """Citya : sitemap des ventes, JSON-LD RealEstateListing et caractéristiques HTML."""

    def __init__(self) -> None:
        super().__init__(
            "citya", "https://www.citya.com/sitemap.vente.xml",
            lambda url: bool(re.search(r"/annonces/vente/(maison|appartement|immeuble)/[^/]+/[^/]+$", url)),
        )

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
        offer = (listing or {}).get("mainEntity") or {}
        item = offer.get("itemOffered") or {}
        kind = re.search(r"/annonces/vente/([a-z]+)/", offer.get("url") or page_url)
        type_hint = TYPES.get(kind.group(1)) if kind else None
        if not listing or not type_hint:
            return
        address = item.get("address") or {}
        floor_size = item.get("floorSize") or {}
        description = item.get("description") or ""
        description = re.sub(r"<br\s*/?>", "\n", description, flags=re.I)
        description = "\n".join(
            line for line in (_text(part) for part in description.split("\n")) if line
        ) or None
        labels = [
            _text(value) for value in re.findall(
                r'<li class="(?:property-characteristics__item[^"]*|inline-flex[^"]*)"[^>]*>(.*?)</li>',
                html, re.S,
            )
        ]
        details: dict = {}
        land_surface = bedrooms = None
        for label in filter(None, labels):
            lowered = label.lower()
            if lowered.startswith("réf"):
                details["Référence"] = label.split(":", 1)[-1].strip()
            elif lowered.startswith("taxe foncière"):
                details["Taxe foncière"] = _euros(label)
            elif lowered.startswith("quote part"):
                details["Charges annuelles de copropriété"] = _euros(label)
            elif lowered.startswith("charges mensuelles"):
                details["Charges mensuelles affichées"] = _euros(label)
            elif lowered.startswith("charges annuelles"):
                # Sur Citya ce montant vise parfois tout l'immeuble : la quote-part prime.
                details["Charges annuelles affichées"] = _euros(label)
            elif lowered.startswith("nombre de lots"):
                details["Nombre de lots"] = int(re.sub(r"\D", "", label) or 0) or None
            elif lowered.startswith("honoraires à la charge"):
                details["Honoraires à la charge"] = label.rsplit(" ", 1)[-1]
            elif "sous compromis" in lowered:
                details["Sous compromis"] = "non" if "pas sous" in lowered else "oui"
            elif "soumis au statut de la copropriété" in lowered:
                details["Copropriété"] = "non" if "non soumis" in lowered else "oui"
            elif lowered.startswith("terrain"):
                surface = re.search(r"([\d\s.,]+)\s*m", label)
                land_surface = float(surface.group(1).replace(" ", "").replace(",", ".")) if surface else None
                details["Terrain"] = label
            elif re.fullmatch(r"\d+ chambres?", lowered):
                bedrooms = int(lowered.split()[0])
            elif ":" in label:
                key, value = label.split(":", 1)
                details[key.strip()] = value.strip()
            elif re.match(r"(construit en|construction) \d{4}", lowered):
                details["Année de construction"] = int(re.search(r"\d{4}", label).group(0))
            elif re.match(r"etage \d+", lowered):
                details["Étage"] = int(re.search(r"\d+", label).group(0))
            elif re.fullmatch(r"\d+ pièces?", lowered) or lowered in TYPES:
                continue
            else:
                details.setdefault("Caractéristiques", []).append(label)
        details = {key: value for key, value in details.items() if value not in (None, "", [], {})}
        dpe = re.search(r'aria-label="Classe énergétique actuelle : ([A-G])"', html)
        ges = re.search(r'aria-label="Classe GES actuelle : ([A-G])"', html)
        consumption = re.search(r"classe [A-G], consommation (\d+) kWh", html)
        if consumption:
            details["DPE consommation"] = int(consumption.group(1))
        emission = re.search(r"classe [A-G], émissions? (\d+) kg", html)
        if emission:
            details["GES émissions"] = int(emission.group(1))
        costs = re.search(r"Entre ([\d\s ]+) € et ([\d\s ]+) € par an", html)
        if costs:
            details["Coût énergétique annuel min"] = float(re.sub(r"\D", "", costs.group(1)))
            details["Coût énergétique annuel max"] = float(re.sub(r"\D", "", costs.group(2)))
        if re.search(r"\bviager\b", f"{item.get('name')} {description}", re.I):
            details["Viager"] = "oui"
        lat = re.search(r'data-latitude="(-?[\d.]+)"', html)
        lng = re.search(r'data-longitude="(-?[\d.]+)"', html)
        images = []
        for path in re.findall(r'/media/images/agences/biens/[^"\'\s)]+?\.(?:webp|jpe?g|png)', html):
            image = "https://www.citya.com" + path
            if image not in images:
                images.append(image)
        agency = re.search(r"Agence référente(.*?)Afficher", html, re.S)
        agency_text = _text(agency.group(1)) if agency else ""
        if agency_text:
            details["Agence"] = agency_text
        reference = details.get("Référence") or page_url.rstrip("/").rsplit("/", 1)[-1]
        yield {
            "id": reference, "name": item.get("name"), "price": offer.get("price"),
            "surface": floor_size.get("value") if isinstance(floor_size, dict) else floor_size,
            "land_surface": land_surface, "rooms": item.get("numberOfRooms"), "bedrooms": bedrooms,
            "zipcode": address.get("postalCode"), "city": address.get("addressLocality"),
            "lat": lat.group(1) if lat else None, "lng": lng.group(1) if lng else None,
            "type_hint": type_hint, "url": page_url, "body": description,
            "dpe": dpe.group(1) if dpe else None, "ges": ges.group(1) if ges else None,
            "seller_name": re.split(r"\s\d", agency_text, 1)[0] or "Citya",
            "published_at": None,
            "details_json": json.dumps(details, ensure_ascii=False),
            "images_json": json.dumps(images, ensure_ascii=False),
            "image_count": len(images), "reference_annonce": reference,
            "raw_payload": json.dumps(listing, ensure_ascii=False, default=str),
        }


register(CityaConnector())
