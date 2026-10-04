from html import unescape
import json
import re
from collections.abc import Iterator

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


def _number(value) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^\d,.-]", "", unescape(str(value)).replace("\xa0", ""))
    return cleaned.replace(",", ".") or None


def _clean_fragment(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value).replace("\xa0", " ")
    return "\n".join(
        re.sub(r"\s+", " ", line).strip()
        for line in value.splitlines() if re.sub(r"\s+", " ", line).strip()
    )


def _page_details(html: str) -> tuple[str | None, dict[str, str], list[str], str | None, str | None]:
    description_match = re.search(
        r'<p[^>]+class=["\'][^"\']*id-desc-body[^"\']*["\'][^>]*>(.*?)</p>',
        html, re.I | re.S,
    )
    description = _clean_fragment(description_match.group(1)) if description_match else None
    details: dict[str, str] = {}
    for block in re.findall(r'<dl[^>]+class=["\'][^"\']*id-spec[^"\']*["\'][^>]*>(.*?)</dl>', html, re.I | re.S):
        key = re.search(r"<dt[^>]*>(.*?)</dt>", block, re.I | re.S)
        value = re.search(r"<dd[^>]*>(.*?)</dd>", block, re.I | re.S)
        if key and value:
            details[_clean_fragment(key.group(1))] = _clean_fragment(value.group(1))
    images = []
    for path in re.findall(r'(?:data-src-lg|href)=["\'](//cdn-immonot[^"\']+/photo/[^"\']+)["\']', html, re.I):
        url = "https:" + unescape(path)
        if url not in images:
            images.append(url)
    def gauge(kind: str) -> str | None:
        match = re.search(
            rf'i-gauge--{kind}\b.*?i-gauge-current["\'][^>]*>\s*([A-G])\s*<',
            html, re.I | re.S,
        )
        return match.group(1).upper() if match else None
    return description, details, images, gauge("dpe"), gauge("ges")


class ImmonotConnector(AgencyJsonLdConnector):
    def __init__(self) -> None:
        super().__init__(
            "immonot", "https://www.immonot.com/sitemap.xml",
            lambda url: (
                "/annonce-immobiliere/" in url
                and "/location-" not in url
                and ("/achat-" in url or "-a-vendre-" in url)
            ),
        )

    def _map_node(self, node: dict, page_url: str) -> dict | None:
        if "/location-" in page_url:
            return None
        node_type = node.get("@type")
        if node_type not in {"Apartment", "House", "Residence", "Product"}:
            return None
        address = node.get("address") or {}
        floor_size = node.get("floorSize") or {}
        action = node.get("potentialAction") or {}
        agent = action.get("agent") or {}
        land_surface = rooms = None
        for feature in node.get("amenityFeature") or []:
            name = str(feature.get("name", "")).lower()
            if name == "land area":
                land_surface = _number(feature.get("value"))
            elif name == "number of rooms":
                rooms = _number(feature.get("value"))
        match = re.search(r"/annonce-immobiliere/([^/]+)/", page_url)
        url_lower = page_url.lower()
        if "terrain" in url_lower:
            type_hint = "Terrain"
        elif "fonds-et-ou-murs-commerciaux" in url_lower:
            type_hint = "LocalCommercial"
        elif "bien-agricole" in url_lower:
            type_hint = "Other"
        elif "appartement" in url_lower:
            type_hint = "Apartment"
        elif "maison" in url_lower:
            type_hint = "House"
        else:
            type_hint = node_type
        return {
            "id": match.group(1) if match else page_url,
            "name": node.get("name"), "price": _number(action.get("price")),
            "surface": _number(floor_size.get("value") if isinstance(floor_size, dict) else floor_size),
            "land_surface": land_surface, "rooms": rooms,
            "zipcode": address.get("postalCode"), "city": address.get("addressLocality"),
            "lat": None, "lng": None, "type_hint": type_hint, "url": page_url,
            "body": node.get("description"), "seller_name": agent.get("name"),
            "raw_payload": json.dumps(node, ensure_ascii=False, default=str),
        }

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        description, details, images, dpe, ges = _page_details(html)
        coordinates = re.search(
            r"var\s+lat\s*=\s*(-?\d+(?:\.\d+)?)\s*var\s+lon\s*=\s*(-?\d+(?:\.\d+)?)",
            html, re.I | re.S,
        )
        if coordinates:
            details["Précision cartographique"] = "Zone approximative publiée par Immonot"
        for item in super().parse_page(html, page_url):
            if description:
                item["body"] = description
            item["details_json"] = json.dumps(details, ensure_ascii=False)
            item["images_json"] = json.dumps(images, ensure_ascii=False)
            item["image_count"] = len(images)
            item["reference_annonce"] = details.get("Référence")
            item["bedrooms"] = _number(details.get("Chambres"))
            item["dpe"] = dpe
            item["ges"] = ges
            if coordinates:
                item["lat"] = coordinates.group(1)
                item["lng"] = coordinates.group(2)
            yield item


register(ImmonotConnector())
