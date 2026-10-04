import json
import re
from html import unescape
from collections.abc import Iterator

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


class PointDeVenteConnector(AgencyJsonLdConnector):
    def __init__(self) -> None:
        super().__init__(
            "pointdevente",
            "https://www.pointdevente.fr/fr/sitemap_llm_annonces.xml",
            lambda url: bool(re.search(r"/p\d+$", url)),
        )

    def _map_node(self, node: dict, page_url: str) -> dict | None:
        if node.get("@type") != "Product":
            return None
        name = node.get("name") or ""
        description = node.get("description") or ""
        lower_url = page_url.lower()
        offers = node.get("offers") or {}
        seller = offers.get("seller") or {}
        surface = re.search(r"([\d,.]+)\s*m(?:²|2)\b", name, re.I)
        postal = re.search(r"\b(\d{5})\b", name)
        external_id = re.search(r"/p(\d+)$", page_url)
        city = None
        if postal:
            after = name[postal.end():].strip(" ,-–")
            city = after.split(" - ", 1)[0].strip() or None
        image_value = node.get("image")
        if isinstance(image_value, dict):
            image_value = image_value.get("url") or image_value.get("contentUrl")
        images = [image_value] if isinstance(image_value, str) else []
        is_rental = (
            "/location/" in lower_url or "/location-pure/" in lower_url
            or name.lower().startswith("location ")
        )
        if "/cession-de-bail-et-fonds-de-commerce/" in lower_url:
            type_hint = "fonds_commerce"
        elif "bureau" in (name + " " + str(node.get("category") or "")).lower():
            type_hint = "bureau"
        else:
            type_hint = "local_commercial"
        return {
            "id": external_id.group(1) if external_id else page_url,
            "name": name, "price": offers.get("price"),
            "surface": surface.group(1).replace(",", ".") if surface else None,
            "land_surface": None, "rooms": None,
            "zipcode": postal.group(1) if postal else None, "city": city,
            "lat": None, "lng": None,
            "type_hint": f"{'location' if is_rental else 'vente'} {type_hint}",
            "url": page_url, "body": description,
            "seller_name": seller.get("name"),
            "details_json": json.dumps({"Catégorie": node.get("category")}, ensure_ascii=False),
            "images_json": json.dumps(images, ensure_ascii=False),
            "image_count": len(images),
            "reference_annonce": external_id.group(1) if external_id else None,
            "raw_payload": json.dumps(node, ensure_ascii=False, default=str),
        }

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        # The JSON-LD only carries the headline. The visible public sheet also
        # exposes the commercial terms that are essential for valuing occupied
        # walls and businesses (rent, charges, fees, frontage and floor split).
        text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
        text = unescape(re.sub(r"<[^>]+>", " ", text)).replace("\xa0", " ")
        text = re.sub(r"[ \t]+", " ", text)
        def capture(pattern: str):
            match = re.search(pattern, text, re.I)
            return re.sub(r"\s+", " ", match.group(1)).strip() if match else None
        details = {
            "Référence commerciale": capture(r"Réf\s*:\s*([\w/-]+)"),
            "Loyer hors charges mensuel": capture(r"Loyer(?:\s+hc)?\s+([\d\s.,]+)\s*€(?:\s*hc)?\s*/?mois"),
            "Charges": capture(r"Charges\s+(.{1,80}?)(?=Honoraires|Localisation|\n|$)"),
            "Honoraires HT": capture(r"Honoraires\s+([\d\s.,]+\s*€\s*HT)"),
            "Façade": capture(r"Façade\s+([\d.,]+\s*m)"),
            "Surface RDC": capture(r"RDC\s+([\d.,]+\s*m[²2])"),
            "Emplacement": capture(r"Emplacement\s*:\s*(.{1,80}?)(?=Analyse|Conditions|\n|$)"),
        }
        details = {key: value for key, value in details.items() if value}
        photos = []
        for url in re.findall(r'https?://[^"\'<>\s]+\.(?:jpe?g|webp|png)(?:\?[^"\'<>\s]*)?', html, re.I):
            url = unescape(url)
            if url not in photos and not any(marker in url.lower() for marker in ("logo", "icon", "sprite")):
                photos.append(url)
        for item in super().parse_page(html, page_url):
            old_details = json.loads(item.get("details_json") or "{}")
            old_details.update(details)
            item["details_json"] = json.dumps(old_details, ensure_ascii=False)
            if details.get("Référence commerciale"):
                item["reference_annonce"] = details["Référence commerciale"]
            if len(photos) > item.get("image_count", 0):
                item["images_json"] = json.dumps(photos, ensure_ascii=False)
                item["image_count"] = len(photos)
            yield item


register(PointDeVenteConnector())
