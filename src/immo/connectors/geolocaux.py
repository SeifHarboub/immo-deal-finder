import json
import re
from collections.abc import Iterator
from html import unescape

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


class GeolocauxConnector(AgencyJsonLdConnector):
    def __init__(self) -> None:
        super().__init__(
            "geolocaux", "https://www.geolocaux.com/sitemap.xml",
            lambda url: "/annonce/" in url,
        )

    def _map_node(self, node: dict, page_url: str) -> dict | None:
        if node.get("@type") != "Product":
            return None
        name = node.get("name") or ""
        description = node.get("description") or ""
        text = f"{page_url} {name} {description}".lower()
        # GeoLocaux est une source strictement professionnelle : les locations
        # de bureaux et de locaux commerciaux restent donc dans le périmètre.
        # La catégorie est recalculée en Silver depuis ces marqueurs explicites.
        is_rental = any(marker in text for marker in (
            "location-", "a-louer", "à louer", "location ",
        ))
        offers = node.get("offers") or {}
        seller = offers.get("seller") or {}
        external_id = re.search(r"-(\d+)\.html$", page_url)
        surface = re.search(r"([\d\s,.]+)\s*m(?:²|2)\b", name, re.I)
        postal = re.search(r"\b(\d{5})\b", name + " " + description)
        displayed_price = offers.get("price")
        is_unit_price = bool(re.search(r"€\s*/\s*m(?:²|2)|euros?\s*/\s*m", description, re.I))
        return {
            "id": external_id.group(1) if external_id else page_url,
            "name": name, "price": None if is_unit_price else displayed_price,
            "displayed_price": displayed_price,
            "price_unit": "eur_m2" if is_unit_price else "total",
            "surface": surface.group(1).replace(" ", "").replace(",", ".") if surface else None,
            "land_surface": None, "rooms": None,
            "zipcode": postal.group(1) if postal else None, "city": None,
            "lat": None, "lng": None,
            "type_hint": f"{'location' if is_rental else 'vente'} {name}",
            "url": page_url,
            "body": description, "seller_name": seller.get("name"),
            "raw_payload": json.dumps(node, ensure_ascii=False, default=str),
        }

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        enriched: dict = {}
        fallback: dict | None = None
        match = re.search(r'<script[^>]+id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.I | re.S)
        if match:
            try:
                values = json.loads(match.group(1))
                meta = next(
                    value for value in values if isinstance(value, dict)
                    and {"url", "reference", "description", "surface", "photos"}.issubset(value)
                )
                def value(key, default=None):
                    index = meta.get(key)
                    return values[index] if isinstance(index, int) and 0 <= index < len(values) else default
                def deref(item):
                    return values[item] if isinstance(item, int) and 0 <= item < len(values) else item
                def structured_list(key):
                    refs = value(key, [])
                    result = []
                    if not isinstance(refs, list):
                        return result
                    for ref in refs:
                        item = deref(ref)
                        if isinstance(item, str):
                            result.append(item)
                        elif isinstance(item, dict):
                            resolved = {str(deref(k)): deref(v) for k, v in item.items()}
                            label = next((resolved.get(k) for k in ("tag", "label", "name", "titre", "title")
                                          if resolved.get(k)), None)
                            content = next((resolved.get(k) for k in ("value", "valeur", "text", "description")
                                            if resolved.get(k)), None)
                            if label and content and label != content:
                                result.append(f"{label}: {content}")
                            elif label or content:
                                result.append(label or content)
                    return result
                def diagnostic(key):
                    result = value(key)
                    return result if str(result).upper() in set("ABCDEFG") else None
                description = value("description")
                if isinstance(description, str):
                    description = re.sub(r"<br\s*/?>", "\n", description, flags=re.I)
                    description = unescape(re.sub(r"<[^>]+>", " ", description))
                    description = "\n".join(re.sub(r"\s+", " ", line).strip() for line in description.splitlines() if line.strip())
                photo_indexes = value("photos", [])
                photos = []
                if isinstance(photo_indexes, list):
                    for photo_index in photo_indexes:
                        photo = values[photo_index] if isinstance(photo_index, int) and photo_index < len(values) else None
                        if isinstance(photo, dict) and isinstance(photo.get("src"), int):
                            filename = values[photo["src"]]
                            photos.append(f"https://www.geolocaux.com/_ipx/f_jpeg&s_1280x960/photos/1280x960/{filename}")
                details = {
                    "Référence": value("reference"), "Nature": value("nature"),
                    "Type": value("type"), "Tarif au m²": value("tarif_m2"),
                    "Prix ou loyer global": value("tarif_mensuel_ou_global"),
                    "Divisible": "Oui" if value("divisible") else None,
                    "Équipements": structured_list("tags") or None,
                    "Fiche technique": structured_list("ficheTechniques") or None,
                    "Publication": value("ago"),
                    "DPE": diagnostic("dpe"), "GES": diagnostic("ges"),
                    "Géolocalisation certifiée": "Oui" if value("certif_geo") else None,
                }
                details = {key: item for key, item in details.items() if item not in (None, "", False)}
                enriched = {
                    "body": description, "price": value("tarif_mensuel_ou_global"),
                    "surface": value("surface"), "lat": value("lat"), "lng": value("lng"),
                    "dpe": diagnostic("dpe"), "ges": diagnostic("ges"),
                    "reference_annonce": value("reference"),
                    "details_json": json.dumps(details, ensure_ascii=False),
                    "images_json": json.dumps(photos, ensure_ascii=False),
                    "image_count": len(photos),
                }
                title = value("titre") or value("title_seo")
                seo_description = value("description_seo", "") or ""
                postal = re.search(r"\b(\d{5})\b", seo_description)
                city = None
                if postal:
                    before = seo_description[:postal.start()].rstrip(" (–-")
                    city_match = re.search(r"\bà\s+([^()–-]+)$", before, re.I)
                    city = city_match.group(1).strip() if city_match else None
                transaction = str(value("type") or "").upper()
                nature = str(value("nature") or "local commercial")
                fallback = {
                    "id": str(value("id") or page_url), "name": title,
                    "price": value("tarif_mensuel_ou_global"),
                    "displayed_price": value("tarif_m2"),
                    "price_unit": "eur_m2" if value("tarif_m2") else "total",
                    "surface": value("surface"), "land_surface": None, "rooms": None,
                    "zipcode": postal.group(1) if postal else None, "city": city,
                    "lat": value("lat"), "lng": value("lng"),
                    "type_hint": f"{'vente' if transaction == 'VEN' else 'location'} {nature}",
                    "url": page_url, "body": description,
                    "dpe": diagnostic("dpe"), "ges": diagnostic("ges"),
                    "seller_name": value("partenaire_name"),
                    "reference_annonce": value("reference"),
                    "details_json": json.dumps(details, ensure_ascii=False),
                    "images_json": json.dumps(photos, ensure_ascii=False),
                    "image_count": len(photos),
                    "raw_payload": json.dumps(meta, ensure_ascii=False, default=str),
                }
            except (ValueError, TypeError, json.JSONDecodeError, StopIteration):
                enriched = {}
                fallback = None
        yielded = False
        for item in super().parse_page(html, page_url):
            item.update({key: value for key, value in enriched.items() if value is not None})
            yielded = True
            yield item
        # GeoLocaux a retiré le JSON-LD Product de certaines fiches en 2026.
        # Leur état Nuxt public contient encore les mêmes données structurées.
        if not yielded and fallback:
            yield fallback


register(GeolocauxConnector())
