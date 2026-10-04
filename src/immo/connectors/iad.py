from collections.abc import Iterator
from html.parser import HTMLParser
import itertools
import json
import re

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


class _StructuredDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.active = False
        self.current: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag == "script" and dict(attrs).get("type") == "application/ld+json":
            self.active = True
            self.current = []

    def handle_data(self, data: str) -> None:
        if self.active:
            self.current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.active:
            self.blocks.append("".join(self.current))
            self.active = False


def _number(value):
    if value is None:
        return None
    match = re.search(r"-?[\d\s\u202f\u00a0]+(?:[,.]\d+)?", str(value))
    return match.group(0).replace(" ", "").replace("\u202f", "").replace("\u00a0", "").replace(",", ".") if match else None


def _nuxt_listing(html: str) -> tuple[list, dict] | tuple[None, None]:
    """Return iad's public, structured listing state embedded in the page."""
    match = re.search(r'<script[^>]+id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.I | re.S)
    if not match:
        return None, None
    try:
        values = json.loads(match.group(1))
    except (json.JSONDecodeError, TypeError):
        return None, None
    listing = next((item for item in values if isinstance(item, dict)
                    and "propertyListingRef" in item and "location" in item), None)
    return (values, listing) if listing else (None, None)


def _deref(values: list, value):
    return values[value] if isinstance(value, int) and 0 <= value < len(values) else value


def _field(values: list, mapping, key, default=None):
    if not isinstance(mapping, dict) or key not in mapping:
        return default
    return _deref(values, mapping[key])


class IadConnector(AgencyJsonLdConnector):
    """Catalogue national iad via ses sitemaps et JSON-LD publics."""

    SITEMAPS = (
        "house", "apartment", "land", "business", "building",
    )

    def __init__(self) -> None:
        super().__init__(
            "iad", "https://www.iadfrance.fr/sitemap/fr/ads.xml",
            lambda url: "/annonce/" in url and "-vente-" in url,
        )

    def _sitemap_urls(self, url: str, seen=None) -> Iterator[str]:
        if url != self.sitemap_url:
            yield from super()._sitemap_urls(url, seen)
            return
        # Un tour de rôle évite que la limite du cycle soit consommée seulement
        # par les maisons, premier fichier et plus gros segment du catalogue.
        streams = [
            iter(AgencyJsonLdConnector._sitemap_urls(self,
                f"https://www.iadfrance.fr/sitemap/fr/ads/{kind}.xml", set()
            ))
            for kind in self.SITEMAPS
        ]
        active = list(streams)
        while active:
            next_active = []
            for stream in active:
                try:
                    yield next(stream)
                    next_active.append(stream)
                except StopIteration:
                    pass
            active = next_active

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        parser = _StructuredDataParser()
        parser.feed(html)
        graph: list[dict] = []
        for block in parser.blocks:
            try:
                payload = json.loads(block)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(payload, dict):
                nodes = payload.get("@graph", [payload])
                if isinstance(nodes, list):
                    graph.extend(item for item in nodes if isinstance(item, dict))

        residence_types = {
            "SingleFamilyResidence", "Apartment", "Residence", "House",
            "Accommodation", "Place", "ApartmentComplex",
        }
        def has_type(item: dict, expected: set[str]) -> bool:
            value = item.get("@type")
            values = set(value if isinstance(value, list) else [value])
            return bool(values.intersection(expected))

        residence = next((item for item in graph if has_type(item, residence_types)), None)
        offer = next((item for item in graph if has_type(item, {"Offer"})), None)
        if not residence or not offer:
            return
        address = residence.get("address") or {}
        floor_size = residence.get("floorSize") or {}
        seller = offer.get("seller") or {}
        description = residence.get("description") or ""
        url_lower = page_url.lower()
        if "/maison-" in url_lower:
            type_hint = "maison"
        elif "/appartement-" in url_lower:
            type_hint = "appartement"
        elif "/terrain-" in url_lower:
            type_hint = "terrain"
        elif "/local-commercial-" in url_lower or "/commerce-" in url_lower:
            type_hint = "local commercial"
        elif "fonds-de-commerce" in url_lower:
            type_hint = "fonds commerce"
        else:
            type_hint = "autre"
        external_id = re.search(r"/r(\d+)(?:$|[/?#])", page_url)
        land = re.search(r"terrain[^.]{0,80}?([\d\s\u202f\u00a0]+)\s*m(?:²|2)", description, re.I)
        dpe = re.search(r"\bDPE\s*[:\-]?\s*([A-G])\b", description, re.I)
        image_ids = {item.get("@id"): item for item in graph if has_type(item, {"ImageObject"})}
        image_ref = residence.get("image") or {}
        image = image_ids.get(image_ref.get("@id"), image_ref) if isinstance(image_ref, dict) else {}
        image_url = image.get("contentUrl") or image.get("url") if isinstance(image, dict) else None
        values, listing = _nuxt_listing(html)
        images = [image_url] if image_url else []
        ges = lat = lng = None
        reference = external_id.group(1) if external_id else None
        published = offer.get("validFrom")
        dpe_class = dpe.group(1).upper() if dpe else None
        details = {"Année de construction": residence.get("yearBuilt")}
        if values and listing:
            reference = _field(values, listing, "propertyListingRef") or reference
            published = _field(values, listing, "firstPublishDate") or published
            description = _field(values, listing, "description") or description
            land = re.search(r"terrain[^.\n]{0,100}?([\d\s\u202f\u00a0]+)\s*m(?:²|2)", description, re.I)
            location = _field(values, listing, "location", {})
            address = {
                "postalCode": _field(values, location, "postcode") or address.get("postalCode"),
                "addressLocality": _field(values, location, "place") or address.get("addressLocality"),
            }
            lat = _field(values, location, "lat", _field(values, location, "latitude"))
            lng = _field(values, location, "lng", _field(values, location, "longitude"))
            epc_ges = _field(values, listing, "epcGes", {})
            epc_data = _field(values, epc_ges, "epc", {})
            ges_data = _field(values, epc_ges, "ges", {})
            costs = _field(values, epc_ges, "amount", {})
            dpe_class = _field(values, epc_data, "class") or dpe_class
            ges = _field(values, ges_data, "class")
            media = _field(values, listing, "media", {})
            photo_refs = _field(values, media, "photos", [])
            if isinstance(photo_refs, list):
                gallery = [_deref(values, ref) for ref in photo_refs]
                images = [url for url in gallery if isinstance(url, str) and url.startswith("http")] or images
            features = []
            for group in ("mainFeatures", "secondaryFeatures"):
                refs = _field(values, listing, group, [])
                if isinstance(refs, list):
                    for ref in refs:
                        text = _field(values, _deref(values, ref), "text")
                        if isinstance(text, str) and not text.startswith("property."):
                            features.append(text)
            prices = _field(values, listing, "prices", {})
            co_ownership = _field(values, listing, "coOwnership", {})
            has_co_ownership = bool(_field(values, listing, "hasCoOwnership"))
            details.update({
                "Année de construction": _field(values, listing, "constructionYear"),
                "Exclusivité": "Oui" if _field(values, listing, "isExclusive") else None,
                "Référence mandat": _field(values, listing, "mandateNumber"),
                "Honoraires à la charge": _field(values, prices, "feesCharge"),
                "Prix au m² annoncé": _field(values, prices, "formattedPerSurface"),
                "DPE consommation": _field(values, epc_data, "consumption"),
                "GES émissions": _field(values, ges_data, "consumption"),
                "Date du diagnostic": _field(values, epc_ges, "date"),
                "Coût énergétique annuel min": _field(values, costs, "min"),
                "Coût énergétique annuel max": _field(values, costs, "max"),
                "Copropriété": "Oui" if has_co_ownership else None,
                "Nombre de lots": _field(values, co_ownership, "totalUnitsCount"),
                "Charges annuelles de copropriété": _field(values, co_ownership, "annualFees"),
                "Procédure de copropriété en cours": (
                    "Oui" if _field(values, co_ownership, "proceedingsUnderway") else "Non"
                ) if has_co_ownership else None,
                "Équipements": features or None,
                "Localisation cartographique": _field(values, location, "mapLabel"),
            })
        details = {key: value for key, value in details.items() if value not in (None, "", [], {})}
        yield {
            "id": reference or page_url,
            "name": residence.get("name"), "price": _number(offer.get("price")),
            "surface": _number(floor_size.get("value") if isinstance(floor_size, dict) else floor_size),
            "land_surface": _number(land.group(1)) if land else None,
            "rooms": residence.get("numberOfRooms"),
            "bedrooms": residence.get("numberOfBedrooms"),
            "zipcode": address.get("postalCode"), "city": address.get("addressLocality"),
            "lat": lat, "lng": lng, "type_hint": type_hint,
            "url": page_url, "body": description, "dpe": dpe_class, "ges": ges,
            "seller_name": seller.get("name"),
            "published_at": published,
            "details_json": json.dumps(details, ensure_ascii=False),
            "images_json": json.dumps(images, ensure_ascii=False),
            "image_count": len(images),
            "reference_annonce": reference,
            "raw_payload": json.dumps({"residence": residence, "offer": offer}, ensure_ascii=False, default=str),
        }


register(IadConnector())
