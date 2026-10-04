from collections.abc import Iterator
from html import unescape
import json
import re

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


SITEMAPS = ("maison", "appartement", "immeuble")
TYPES = {"maison": "maison", "appartement": "appartement", "immeuble": "immeuble"}


def _flight_text(html: str) -> str:
    """Concatène les fragments du flux RSC Next.js (`self.__next_f.push`)."""
    text = []
    for chunk in re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html, re.S):
        try:
            text.append(json.loads(f'"{chunk}"'))
        except json.JSONDecodeError:
            continue
    return "".join(text)


def _enclosing_object(text: str, key: str) -> dict | None:
    """Remonte depuis `key` jusqu'à l'accolade ouvrante de l'objet qui la contient."""
    index = text.find(key)
    depth = 0
    decoder = json.JSONDecoder()
    while index > 0:
        index -= 1
        char = text[index]
        if char == "}":
            depth += 1
        elif char == "{":
            if depth:
                depth -= 1
                continue
            try:
                value = decoder.raw_decode(text[index:])[0]
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and key.strip('"') in value:
                return value
    return None


def _flight_ref(text: str, value):
    """Résout une référence RSC `$3a` vers son bloc texte `3a:T<hex>,...`."""
    if not isinstance(value, str) or not re.fullmatch(r"\$[0-9a-f]+", value):
        return value
    match = re.search(rf"(?:^|[\n}}\]\"]){value[1:]}:T([0-9a-f]+),", text)
    if not match:
        return None
    size = int(match.group(1), 16)
    return text[match.end():].encode("utf-8")[:size].decode("utf-8", errors="ignore")


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", " ", text)).replace("\xa0", " ")
    return "\n".join(re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()) or None


def _amount(value) -> float | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


class SaftiConnector(AgencyJsonLdConnector):
    """Réseau SAFTI : sitemaps des biens disponibles puis état RSC de la fiche."""

    def __init__(self) -> None:
        super().__init__(
            "safti", "https://www.safti.fr/sitemaps/sitemap.annonce.maison.disponible.xml",
            lambda url: "/annonces/achat/" in url,
        )

    def _sitemap_urls(self, url: str, seen=None) -> Iterator[str]:
        if url != self.sitemap_url:
            yield from super()._sitemap_urls(url, seen)
            return
        # Tour de rôle : la limite de cycle ne doit pas être absorbée par les maisons.
        streams = [
            iter(AgencyJsonLdConnector._sitemap_urls(
                self, f"https://www.safti.fr/sitemaps/sitemap.annonce.{kind}.disponible.xml", set()
            ))
            for kind in SITEMAPS
        ]
        while streams:
            alive = []
            for stream in streams:
                try:
                    yield next(stream)
                    alive.append(stream)
                except StopIteration:
                    pass
            streams = alive

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        text = _flight_text(html)
        data = _enclosing_object(text, '"propertySurface"')
        if not data or data.get("adType") not in (None, "vente") or data.get("sold"):
            return
        type_hint = TYPES.get(str(data.get("propertyType") or "").lower())
        if not type_hint:
            return
        description = _clean(_flight_ref(text, data.get("description")))
        if not description:
            for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
                try:
                    node = json.loads(block)
                except json.JSONDecodeError:
                    continue
                if isinstance(node, dict) and node.get("@type") == "Product":
                    description = _clean(node.get("description"))
        images = [
            photo.get("urlPhotoLarge") for photo in data.get("photos") or []
            if isinstance(photo, dict) and photo.get("urlPhotoLarge")
        ]
        lots = re.search(r"copropriété de (\d+) lots", description or "", re.I)
        agent = data.get("agent") or {}
        details = {
            "Référence": data.get("propertyReference"),
            "Année de construction": data.get("yearConstruction") or None,
            "Étage": data.get("floor"), "Nombre d'étages": data.get("floorNumber") or None,
            "Salles de bain": data.get("bathroomNumber") or None,
            "WC": data.get("wcNumber") or None,
            "Places de parking": data.get("carParkNumber") or None,
            "Chauffage": data.get("heaterType") or None,
            "Jardin": "oui" if data.get("garden") else None,
            "Meublé": "oui" if data.get("furnished") else None,
            "Cuisine équipée": "oui" if data.get("equippedKitchen") else None,
            "Exclusivité": "oui" if data.get("exclusiveMandate") else None,
            "Prestige": "oui" if data.get("isPrestige") else None,
            "Sous compromis": "oui" if data.get("underCompromise") else None,
            "Prix en baisse": "oui" if data.get("reducedPrice") else None,
            "Honoraires": _amount(data.get("agencyFees")),
            "Honoraires à la charge": {"seller": "vendeur", "buyer": "acquéreur"}.get(
                data.get("agencyFeesType"), data.get("agencyFeesType")),
            "Taxe foncière": _amount(data.get("propertyTax")),
            "Charges annuelles de copropriété": _amount(data.get("condominiumFees")),
            "Nombre de lots": int(lots.group(1)) if lots else None,
            "DPE consommation": data.get("dpeValue") or None,
            "GES émissions": data.get("gesValue") or None,
            "Coût énergétique annuel min": _amount(data.get("dpeConsumptionMin")),
            "Coût énergétique annuel max": _amount(data.get("dpeConsumptionMax")),
            "Date du DPE": data.get("dpeExecutionDate"),
            "Accroche": data.get("catchphrase") or None,
            "Points forts": data.get("strongPoint") or None,
            "Activité commerciale": data.get("commercialActivity") or None,
            "Département": data.get("departement"), "Région": data.get("region"),
            "Viager": "oui" if re.search(r"\bviager\b", description or "", re.I) else None,
        }
        for group, values in (data.get("amenagements") or {}).items():
            if isinstance(values, list) and values:
                details[group] = values
        details = {key: value for key, value in details.items() if value not in (None, "", [], {})}
        reference = str(data.get("propertyReference") or "") or None
        catchphrase = data.get("catchphrase")
        yield {
            "id": reference or page_url,
            "name": catchphrase or f"{type_hint.capitalize()} {data.get('city') or ''}".strip(),
            "price": data.get("price"), "surface": data.get("propertySurface") or None,
            "land_surface": data.get("areaSurface") or None,
            "rooms": data.get("roomNumber") or None, "bedrooms": data.get("bedroomNumber"),
            "zipcode": data.get("postCode"), "city": data.get("city"),
            "lat": data.get("lat"), "lng": data.get("lng"), "type_hint": type_hint,
            "url": page_url, "body": description,
            "dpe": data.get("dpeNote") if data.get("dpeNote") in tuple("ABCDEFG") else None,
            "ges": data.get("gesNote") if data.get("gesNote") in tuple("ABCDEFG") else None,
            "seller_name": " ".join(filter(None, [agent.get("firstName"), agent.get("lastName")])) or None,
            "published_at": data.get("diffusionDate"),
            "details_json": json.dumps(details, ensure_ascii=False),
            "images_json": json.dumps(images, ensure_ascii=False),
            "image_count": len(images), "reference_annonce": reference,
            "raw_payload": json.dumps(
                {key: value for key, value in data.items() if key not in {"photos", "agent", "multiLangFields"}},
                ensure_ascii=False, default=str,
            ),
        }


register(SaftiConnector())
