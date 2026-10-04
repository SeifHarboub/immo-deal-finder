from collections.abc import Iterator
from html import unescape
import json
import re

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


TYPES = {
    "maison": "maison", "appartement": "appartement", "immeuble": "immeuble",
    "terrain": "terrain", "local": "local_commercial", "commerce": "local_commercial",
    "bureau": "bureau", "fonds": "fonds_commerce", "divers": "autre",
}
# Seuils DPE 2021 (métropole) : la classe affichée est la pire des deux échelles.
ENERGY_LIMITS = (70, 110, 180, 250, 330, 420)
CARBON_LIMITS = (6, 11, 30, 50, 70, 100)


def _text(fragment: str) -> str:
    fragment = re.sub(r"<(script|svg|style)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    fragment = re.sub(r"<br\s*/?>|</p>|</li>", "\n", fragment, flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ").replace(" ", " ")
    return "\n".join(re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip())


def _number(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"\d[\d\s ]*(?:,\d+)?", value)
    return float(re.sub(r"[\s ]", "", match.group(0)).replace(",", ".")) if match else None


def _section(html: str, name: str) -> str:
    match = re.search(rf'<section class="c-the-property-detail-{name}".*?</section>', html, re.S)
    return match.group(0) if match else ""


def _letter(value: float | None, limits: tuple) -> int | None:
    if value is None:
        return None
    return next((index for index, limit in enumerate(limits) if value <= limit), len(limits))


class Century21Connector(AgencyJsonLdConnector):
    """CENTURY 21 : sitemap des fiches de vente, page HTML server-side."""

    def __init__(self) -> None:
        super().__init__(
            "century21", "https://www.century21.fr/sitemap-vente_detail.xml",
            lambda url: "/trouver_logement/detail/" in url,
        )

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        title_match = re.search(r"<title>(.*?)</title>", html, re.S)
        title = unescape(title_match.group(1)).strip() if title_match else ""
        if "à vendre" not in title.lower():
            return
        kind = title.split(" ", 1)[0].lower()
        if kind in {"parking", "garage", "box"}:
            return
        type_hint = TYPES.get(kind, "autre")
        price_match = re.search(r'c-the-property-abstract__price\b[^"]*"[^>]*>(.*?)</p>', html, re.S)
        price = _number(_text(price_match.group(1))) if price_match else None
        description = _text(_section(html, "description"))
        description = re.sub(r"^Description\s*", "", description).strip() or None
        if re.search(r"\b(programme neuf|VEFA|frais de notaire réduits)\b", description or "", re.I):
            return
        global_items = [
            _text(item) for item in re.findall(r"<li[^>]*>(.*?)</li>", _section(html, "global-view"), re.S)
        ]
        values = {}
        rooms_detail = []
        for item in global_items:
            first = item.split("\n", 1)[0]
            if " : " in first:
                if len(item.split("\n")) > 1:
                    rooms_detail.extend(line for line in item.split("\n")[1:] if "(" in line)
                key, value = first.split(" : ", 1)
                values[key.strip()] = value.strip()
            elif re.match(r".+\([\d,]+ m ?2 ?\)$", first):
                rooms_detail.append(first)
        details: dict = {}
        for key, value in values.items():
            if key not in {"Surface habitable", "Surface terrain", "Nombre de pièces"}:
                details[key] = value
        if rooms_detail:
            details["Détail des pièces"] = rooms_detail
        equipment = _text(_section(html, "equipment")).splitlines()
        extras = [line for line in equipment if line not in {"Équipements", "Les plus", "Général"}]
        for line in extras:
            if " : " in line:
                key, value = line.split(" : ", 1)
                details[key.strip()] = value.strip()
            else:
                details.setdefault("Équipements", [])
                if line not in details["Équipements"]:
                    details["Équipements"].append(line)
        for line in _text(_section(html, "to-know")).splitlines():
            if " : " not in line:
                continue
            key, value = (part.strip() for part in line.split(" : ", 1))
            if key.lower().startswith("taxe foncière"):
                details["Taxe foncière"] = _number(value)
            elif "charges" in key.lower() and "an" in key.lower():
                details["Charges annuelles de copropriété"] = _number(value)
            elif "lots" in key.lower():
                details["Nombre de lots"] = _number(value)
            else:
                details[key] = value
        for mention in re.findall(r'c-the-property-abstract__price-mentions[^>]*>(.*?)</li>', html, re.S):
            mention = _text(mention)
            if "honoraires charge" in mention.lower():
                details["Honoraires à la charge"] = "acquéreur" if "acquéreur" in mention.lower() else "vendeur"
                rate = re.search(r"([\d,]+)\s*%", mention)
                if rate:
                    details["Honoraires (%)"] = float(rate.group(1).replace(",", "."))
            elif "hors honoraires" in mention.lower():
                details["Prix hors honoraires"] = _number(mention)
        dpe = ges = None
        # Les valeurs DPE sont dans le SVG : texte brut sans retirer les <svg>.
        energy_text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", _section(html, "dpe"))))
        energy = re.search(r"\(énergie primaire\)\s*(\d+)\s*(\d+)\s*kWh", energy_text)
        dpe_date = re.search(r"Date du DPE : ([\d/]+)", energy_text)
        surface = _number(values.get("Surface habitable")) or _number(values.get("Surface totale"))
        if energy:
            consumption, carbon = float(energy.group(1)), float(energy.group(2))
            details.update({"DPE consommation": consumption, "GES émissions": carbon})
            # Les petites surfaces ont des seuils spécifiques : pas de recalcul sous 40 m².
            if not surface or surface >= 40:
                dpe = "ABCDEFG"[max(_letter(consumption, ENERGY_LIMITS), _letter(carbon, CARBON_LIMITS))]
                details["Classe DPE"] = "recalculée depuis les valeurs affichées (seuils 2021)"
            ges = "ABCDEFG"[_letter(carbon, CARBON_LIMITS)]
        if dpe_date:
            details["Date du DPE"] = dpe_date.group(1)
        crumbs = [unescape(name) for name in re.findall(r'<span itemprop="name">([^<]*)</span>', html)]
        place = next((
            match for crumb in reversed(crumbs)
            if (match := re.search(r"([^()]+?)\s*\((\d{5})\)$", crumb))
        ), None)
        city = zipcode = None
        if place:
            city = re.sub(r"^Achat\s+\S+\s+", "", place.group(1)).strip()
            zipcode = place.group(2)
        reference = re.search(r"Ref\s*:\s*([\w-]+)", html)
        reference = reference.group(1) if reference else None
        listing_id = re.search(r"/detail/(\d+)/", page_url)
        # Chaque photo existe en `_1_` (grande) et `_8_` (vignette) : une par UUID.
        photos: dict[str, str] = {}
        for path in re.findall(r'data-src="(/imagesBien/s3/[^"]+\.jpe?g)"', html):
            if reference and f"_{reference}_" not in path:
                continue
            key = path.rsplit("_", 1)[-1]
            if key not in photos or f"_{reference}_1_" in path:
                photos[key] = "https://www.century21.fr" + path
        images = list(photos.values())
        agency = re.search(r"Ce bien est proposé par l'agence\s*</[^>]+>\s*<[^>]+>([^<]+)<", html)
        if re.search(r"\bviager\b", f"{title} {description}", re.I):
            details["Viager"] = "oui"
        details["Référence"] = reference
        details = {key: value for key, value in details.items() if value not in (None, "", [], {})}
        bedrooms = sum(1 for item in rooms_detail if item.lower().startswith("chambre")) or None
        yield {
            "id": listing_id.group(1) if listing_id else page_url, "name": title.split(" - ")[0],
            "price": price, "surface": surface,
            "land_surface": _number(values.get("Surface terrain")),
            "rooms": _number(values.get("Nombre de pièces")), "bedrooms": bedrooms,
            "zipcode": zipcode, "city": city.title() if city else None, "lat": None, "lng": None,
            "type_hint": type_hint, "url": page_url, "body": description, "dpe": dpe, "ges": ges,
            "seller_name": agency.group(1).strip() if agency else None, "published_at": None,
            "seller_type": "pro",
            "details_json": json.dumps(details, ensure_ascii=False),
            "images_json": json.dumps(images, ensure_ascii=False),
            "image_count": len(images), "reference_annonce": reference,
            "raw_payload": json.dumps({"title": title, "values": values}, ensure_ascii=False),
        }


register(Century21Connector())
