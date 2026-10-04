from collections.abc import Iterator
from html import unescape
import json
import os
import random
import re
import threading
import time

import requests

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


TYPES = {
    "appartement": "appartement", "maison": "maison", "immeuble": "immeuble",
    "terrain": "terrain", "local-commercial": "local_commercial", "local commercial": "local_commercial",
    "boutique": "local_commercial", "bureau": "bureau", "bureaux": "bureau",
    "fonds-de-commerce": "fonds_commerce", "fonds de commerce": "fonds_commerce",
    "propriete": "maison", "propriété": "maison", "chateau": "maison", "château": "maison",
    "villa": "maison", "loft": "appartement", "duplex": "appartement",
}
SKIPPED_TYPES = {"parking", "garage", "box", "cave"}
NUXT_WRAPPERS = {"Reactive", "ShallowReactive", "Ref", "ShallowRef", "NuxtError", "Island"}


def revive_nuxt(payload: list):
    """Reconstitue l'arbre `__NUXT_DATA__` (tableau indexé de devalue)."""
    memo: dict[int, object] = {}

    def revive(index: int):
        if index in memo:
            return memo[index]
        value = payload[index]
        if isinstance(value, list):
            if value and isinstance(value[0], str) and value[0] in NUXT_WRAPPERS | {"Date", "EmptyRef", "Set", "Map"}:
                tag = value[0]
                if tag == "Date":
                    result = value[1]
                elif tag == "EmptyRef":
                    result = None
                elif tag == "Set":
                    result = [revive(item) for item in value[1:]]
                elif tag == "Map":
                    result = {str(revive(value[i])): revive(value[i + 1]) for i in range(1, len(value) - 1, 2)}
                else:
                    result = revive(value[1])
                memo[index] = result
                return result
            result = []
            memo[index] = result
            result.extend(revive(item) if isinstance(item, int) else item for item in value)
            return result
        if isinstance(value, dict):
            result = {}
            memo[index] = result
            for key, item in value.items():
                result[key] = revive(item) if isinstance(item, int) and not isinstance(item, bool) else item
            return result
        return value

    return revive(0)


def _classified(html: str) -> dict | None:
    match = re.search(r'<script[^>]+id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        return None
    tree = revive_nuxt(json.loads(match.group(1)))
    data = tree.get("data") if isinstance(tree, dict) else None
    for value in (data or {}).values():
        if isinstance(value, dict) and isinstance(value.get("classified"), dict):
            return value["classified"]
    return None


def _decimals(value) -> int:
    text = str(value)
    return len(text.split(".", 1)[1]) if "." in text else 0


class FigaroConnector(AgencyJsonLdConnector):
    """Figaro Immobilier : sitemaps d'annonces et état Nuxt des fiches.

    Le catalogue mélange ventes, locations et neuf, avec des pages d'environ
    1,3 Mo. Le titre en tête de page suffit à écarter une location : la lecture
    est alors interrompue après quelques kilo-octets. Un défi Cloudflare n'est
    jamais contourné : après plusieurs défis consécutifs le connecteur se met en
    pause pour le reste du cycle.
    """

    PARSER_VERSION = 4
    SITEMAP_INDEX = "https://immobilier.lefigaro.fr/sitemap_index.xml"

    def __init__(self) -> None:
        super().__init__(
            "figaro", self.SITEMAP_INDEX,
            lambda url: bool(re.search(r"/annonces/annonce-\d+\.html$", url)),
        )
        self._challenges = 0
        self._challenge_lock = threading.Lock()

    def _sitemap_urls(self, url: str, seen=None) -> Iterator[str]:
        if url != self.sitemap_url:
            yield from super()._sitemap_urls(url, seen)
            return
        count = int(os.getenv("FIGARO_CLASSIFIED_SITEMAPS", "12"))
        for index in range(1, count + 1):
            yield from super()._sitemap_urls(
                f"https://immobilier.lefigaro.fr/sitemap/fi/index/sitemap_classifieds_{index}.xml", seen
            )

    def _fetch_detail(self, url: str) -> tuple[str, str] | None:
        limit = int(os.getenv("FIGARO_CHALLENGE_LIMIT", "5"))
        with self._challenge_lock:
            if self._challenges >= limit:
                return None
        minimum = float(os.getenv("SCRAPE_MIN_DELAY_FIGARO", os.getenv("SCRAPE_MIN_DELAY", "1")))
        maximum = float(os.getenv("SCRAPE_MAX_DELAY_FIGARO", os.getenv("SCRAPE_MAX_DELAY", "2")))
        if maximum > 0:
            time.sleep(random.uniform(max(0, minimum), max(minimum, maximum)))
        timeout = float(os.getenv("AGENCY_TIMEOUT_FIGARO", os.getenv("AGENCY_TIMEOUT", "25")))
        for attempt in range(2):
            try:
                with self._detail_session().get(url, timeout=timeout, stream=True) as response:
                    if response.status_code == 404 or response.status_code == 410:
                        return None
                    if response.status_code == 403 or response.headers.get("cf-mitigated"):
                        with self._challenge_lock:
                            self._challenges += 1
                        return None
                    if response.status_code in {429, 500, 502, 503, 504}:
                        time.sleep(3 * (attempt + 1))
                        continue
                    response.raise_for_status()
                    with self._challenge_lock:
                        self._challenges = 0
                    body = bytearray()
                    head_checked = False
                    for chunk in response.iter_content(65536):
                        body.extend(chunk)
                        if not head_checked and b"</title>" in body:
                            head_checked = True
                            title = re.search(rb"<title>(.*?)</title>", body, re.S)
                            label = unescape(title.group(1).decode("utf-8", "ignore")) if title else ""
                            if re.match(r"\s*(location|colocation|programme|neuf)\b", label, re.I):
                                # En-tête seul : parse_page ne produit rien et l'URL
                                # entre dans le cache négatif, sans lire 1,3 Mo.
                                return body.decode("utf-8", "ignore"), response.url
                    return body.decode("utf-8", "ignore"), response.url
            except requests.RequestException:
                time.sleep(2 * (attempt + 1))
        return None

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        data = _classified(html)
        if not data:
            return
        transaction = str(data.get("transaction") or "").lower()
        if transaction not in {"vente", "viager"} or data.get("program"):
            return
        kind = str(data.get("type") or "").lower()
        if kind in SKIPPED_TYPES or "neuf" in kind:
            return
        type_hint = TYPES.get(kind, "autre")
        location = data.get("location") or {}
        prices = data.get("priceData") or {}
        condo = data.get("condominium") or {}
        dpe = data.get("dpe") or {}
        client = data.get("client") or {}
        title = re.search(r"<title>(.*?)</title>", html, re.S)
        title = unescape(title.group(1)).split(" : Figaro")[0].strip() if title else None
        zipcode = location.get("postalCode") or (
            re.search(r"\((\d{5})\)", title).group(1) if title and re.search(r"\((\d{5})\)", title) else None
        )
        lat, lng = location.get("latitude"), location.get("longitude")
        tax = prices.get("propertyTax") or {}
        details = {
            "Code INSEE": location.get("inseeCode"), "Département": location.get("department"),
            "Référence": data.get("reference"), "Identifiant Figaro": data.get("id"),
            "Historique des prix": prices.get("history") or None,
            "Baisse de prix (%)": data.get("priceDownPercentage") or None,
            "Prix net vendeur": prices.get("netSellingPrice"),
            "Honoraires": prices.get("agencyFees") or None,
            "Honoraires à la charge": prices.get("feesPayingAgent"),
            "Prix au m² annoncé": prices.get("m2Price"),
            "Marge de négociation estimée (%)": prices.get("priceNegotiationRate"),
            "Taxe foncière": tax.get("value") if isinstance(tax, dict) and not tax.get("computed") else None,
            "Taxe foncière estimée": tax.get("value") if isinstance(tax, dict) and tax.get("computed") else None,
            "Copropriété": "oui" if condo.get("isCondominium") else None,
            "Procédure de copropriété en cours": "oui" if condo.get("ongoingRecourse") else None,
            "Exposition": data.get("exposure"), "Chauffage": data.get("heatingType"),
            "Salles d'eau": data.get("showerRoomCount") or None,
            "Salles de bain": data.get("bathRoomCount") or None,
            "Étage": data.get("floor"), "Meublé": "oui" if data.get("isFurnished") else None,
            "Exclusivité": "oui" if data.get("isExclusive") else None,
            "Options": data.get("options") or None,
            "Coût énergétique annuel": dpe.get("energyCost") or None,
            "DPE consommation": dpe.get("energy") or None, "GES émissions": dpe.get("ges") or None,
            "Date du DPE": dpe.get("dateDpeFr"), "Origine": data.get("origin"),
            "Mise à jour": data.get("updatedAt"),
            "Viager": "oui" if transaction == "viager" or re.search(
                r"\bviager\b", f"{title} {data.get('descriptionFull')}", re.I) else None,
        }
        for key, value in condo.items():
            lowered = key.lower()
            if "lot" in lowered and isinstance(value, (int, float)):
                details["Nombre de lots"] = value
            elif "charge" in lowered and isinstance(value, (int, float)) and value > 0:
                details["Charges annuelles de copropriété"] = value * 12 if "month" in lowered else value
        if location.get("geocoded") is False or max(_decimals(lat), _decimals(lng)) <= 2:
            details["Précision cartographique"] = "approximative"
        details = {key: value for key, value in details.items() if value not in (None, "", [], {})}
        photos = (data.get("images") or {}).get("photos") or []
        images = [
            (photo.get("url") or {}).get("large") for photo in photos
            if isinstance(photo, dict) and (photo.get("url") or {}).get("large")
        ]
        rooms = data.get("roomCount")
        rooms = rooms[0] if isinstance(rooms, list) and rooms else rooms
        identifier = str(data.get("id") or "") or None
        energy = str(dpe.get("energyCategory") or "").upper()
        carbon = str(dpe.get("gesCategory") or "").upper()
        yield {
            "id": identifier or page_url, "name": title, "price": data.get("price"),
            "surface": data.get("area") or None, "land_surface": data.get("areaGround") or None,
            "rooms": rooms, "bedrooms": data.get("bedRoomCount"),
            "zipcode": zipcode, "city": location.get("cityOnly"), "lat": lat, "lng": lng,
            "type_hint": type_hint, "url": page_url,
            "body": data.get("descriptionFull") or data.get("description"),
            "dpe": energy if energy in tuple("ABCDEFG") else None,
            "ges": carbon if carbon in tuple("ABCDEFG") else None,
            "seller_name": client.get("brandName") or client.get("name"),
            "published_at": data.get("firstPublicationDate") or data.get("creationDate"),
            "seller_type": "private" if str(data.get("origin") or "").lower() == "particulier" else "pro",
            "details_json": json.dumps(details, ensure_ascii=False),
            "images_json": json.dumps(images, ensure_ascii=False),
            "image_count": len(images), "reference_annonce": data.get("reference") or identifier,
            "raw_payload": json.dumps(
                {key: value for key, value in data.items()
                 if key not in {"images", "environmentalScores", "clientImage", "linkedOptions", "location"}},
                ensure_ascii=False, default=str,
            ),
        }


register(FigaroConnector())
