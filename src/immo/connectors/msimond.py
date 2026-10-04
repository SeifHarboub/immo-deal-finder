"""Michel Simond : fonds de commerce, hôtels, entreprises et murs professionnels.

Le serveur n'envoie pas le certificat intermédiaire Sectigo : la vérification TLS
reste active avec un bundle `certifi` + `config/sectigo-ov-r36.pem` (jamais
`verify=False`). Le sitemap liste les fiches `/acheter/{rubrique}/{slug}`.
"""

from collections.abc import Iterator
from html import unescape
import json
import os
from pathlib import Path
import random
import re
import tempfile
import threading
import time

import certifi
import requests

from immo.connectors.agency import USER_AGENT, AgencyJsonLdConnector
from immo.connectors.base import register


BASE = "https://www.msimond.fr"
_URL = re.compile(r"^https://www\.msimond\.fr/acheter/(commerce|immobilier-entreprise|hotellerie|entreprise)/([a-z0-9-]+)/?$")
INTERMEDIATE = Path(__file__).resolve().parents[3] / "config" / "sectigo-ov-r36.pem"
_BUNDLE_LOCK = threading.Lock()
_BUNDLE: str | None = None


def ca_bundle() -> str:
    """Bundle certifi complété par l'intermédiaire manquant, construit une fois par processus."""
    global _BUNDLE
    with _BUNDLE_LOCK:
        if _BUNDLE is None:
            if not INTERMEDIATE.exists():
                _BUNDLE = certifi.where()
            else:
                path = Path(tempfile.gettempdir()) / "immo-msimond-ca-bundle.pem"
                path.write_text(
                    Path(certifi.where()).read_text(encoding="utf-8") + "\n"
                    + INTERMEDIATE.read_text(encoding="utf-8"), encoding="utf-8",
                )
                _BUNDLE = str(path)
        return _BUNDLE


def _text(html: str | None) -> str:
    text = re.sub(r"<br\s*/?>|</p>", "\n", html or "", flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", " ", text)).replace("\xa0", " ").replace(" ", " ")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def _euros(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"(\d[\d\s.]*(?:,\d+)?)\s*(k|m)?\s*€", value.replace(" ", " ").replace("\xa0", " "), re.I)
    if not match:
        return None
    number = float(re.sub(r"[\s.]", "", match.group(1)).replace(",", "."))
    unit = (match.group(2) or "").lower()
    return number * (1_000 if unit == "k" else 1_000_000 if unit == "m" else 1) or None


class MichelSimondConnector(AgencyJsonLdConnector):
    PARSER_VERSION = 1

    def __init__(self) -> None:
        super().__init__("msimond", f"{BASE}/sitemap.xml", lambda url: bool(_URL.match(url)))
        for key, value in (("SCRAPE_MIN_DELAY_MSIMOND", "0.5"),
                           ("SCRAPE_MAX_DELAY_MSIMOND", "1.0"),
                           ("AGENCY_FETCH_WORKERS_MSIMOND", "4")):
            os.environ.setdefault(key, value)
        self.session.verify = ca_bundle()
        self._local = threading.local()

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers["User-Agent"] = USER_AGENT
            session.verify = ca_bundle()
            self._local.session = session
        return session

    def _fetch_detail(self, url: str) -> tuple[str, str] | None:
        # Même politique que le socle (délai, reprises, Retry-After) avec le bundle TLS.
        retries = max(0, int(os.getenv("AGENCY_RETRIES_MSIMOND", os.getenv("AGENCY_RETRIES", "2"))))
        timeout = float(os.getenv("AGENCY_TIMEOUT_MSIMOND", os.getenv("AGENCY_TIMEOUT", "25")))
        minimum = float(os.getenv("SCRAPE_MIN_DELAY_MSIMOND", "0.5"))
        maximum = float(os.getenv("SCRAPE_MAX_DELAY_MSIMOND", "1.0"))
        if maximum > 0:
            time.sleep(random.uniform(max(0, minimum), max(minimum, maximum)))
        for attempt in range(retries + 1):
            try:
                response = self._session().get(url, timeout=timeout)
                if response.status_code in (404, 410):
                    return None
                if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
                    if attempt < retries:
                        retry_after = response.headers.get("Retry-After", "")
                        time.sleep(min(float(retry_after) if retry_after.isdigit() else 2.0 * (attempt + 1), 15))
                        continue
                    return None
                response.raise_for_status()
                return response.text, response.url
            except requests.RequestException:
                if attempt >= retries:
                    return None
                time.sleep(min(2.0 * (attempt + 1), 15))
        return None

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        record = parse_detail(html, page_url.split("?", 1)[0].rstrip("/"))
        if record:
            yield record


def parse_detail(html: str, url: str) -> dict | None:
    match = _URL.match(url)
    title = re.search(r'<h1 class="card-annonce-title">(.*?)</h1>', html, re.S)
    if not match or not title:
        return None
    rubric, slug = match.groups()
    offer = re.search(r'data-offer-id="(\d+)"', html[:html.find("card-annonce-title")])
    city_text = re.search(r'<p class="card-annonce-city">(.*?)</p>', html, re.S)
    city_text = _text(city_text.group(1)) if city_text else ""
    prices = [re.sub(r"\s+", " ", _text(value)) for value in re.findall(
        r'<div class="card-annonce-(?:price|tech)-wrapper">(.*?)</div>', html, re.S)]
    body = re.search(r'<p class="annonce-content-text">(.*?)</p>', html, re.S)
    summary = re.search(r'annonce-content-paragraphe-title">En résumé</p>(.*?)</div>', html, re.S)
    lines = [_text(line) for line in re.findall(r"<p[^>]*>(.*?)</p>", summary.group(1), re.S)] if summary else []
    lines = [line for line in lines if line]
    reference = re.search(r'paragraphe-title">Référence annonce</p>\s*<p>([^<]+)</p>', html)
    offer_ref = re.search(r'<h2 class="offer-ref">(.*?)</h2>', html, re.S)
    offer_ref = _text(offer_ref.group(1)) if offer_ref else ""
    details: dict = {"Rubrique": rubric}
    price = walls = None
    for value in prices:
        if re.match(r"Murs\s*:", value):
            walls = _euros(value)
        elif re.match(r"Prix", value):
            price = _euros(value)
            if "murs inclus" in value.lower():
                details["Prix murs inclus"] = "oui"
            elif "prix des murs" in value.lower():
                walls = walls or price
    if walls:
        details["Prix des murs"] = walls
        if price and details.get("Prix murs inclus") and price > walls:
            details["Prix du fonds"] = price - walls
        elif price and price != walls:
            details["Prix du fonds"] = price
    nature = lines[0] if lines and ":" not in lines[0] else ""
    surface = None
    for line in lines:
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key.startswith("chiffre d'affaires"):
            details["Chiffre d'affaires"] = _euros(value)
        elif "ebe" in key or "brut d" in key:
            details["EBE"] = _euros(value)
        elif key.startswith("résultat") or key.startswith("resultat"):
            details["Résultat net"] = _euros(value)
        elif key.startswith("effectif"):
            details["Effectif"] = value.strip()
        elif key.startswith("surface"):
            found = re.search(r"([\d\s.,]+)\s*m", value)
            surface = float(re.sub(r"\s", "", found.group(1)).replace(",", ".")) if found else None
        elif key.startswith("loyer") and _euros(value):
            details["Loyer mentionné"] = value.strip()
    details["Nature"] = nature or None
    activity = re.search(r'"([^"]+)"', offer_ref)
    if activity:
        details["Secteur"] = activity.group(1)
    geo = re.search(r"([^\n]+?)\s+-\s+([^\n]+?)\s+-\s+(\d{2}|2[AB]|97\d)\s*$", offer_ref)
    if geo:
        details["Région"], details["Département"] = geo.group(1).split("\n")[-1].strip(), geo.group(3)
    postal = re.search(r"\((\d{5})\)", city_text)
    dept = re.search(r"\((\d{2}|2[AB]|97\d)\)\s*$", city_text)
    if dept and "Département" not in details:
        details["Département"] = dept.group(1)
    city = city_text.split("(")[0].strip().title() if postal else None
    nature_lower = f"{nature} {offer_ref}".lower()
    if rubric == "immobilier-entreprise" or nature_lower.strip().startswith("murs"):
        heading = f"{title.group(1)} {offer_ref}".lower()
        type_bien = ("bureau" if "bureau" in heading else "terrain" if "terrain" in heading
                     else "autre" if re.search(r"activit|entrep|atelier|industri", heading)
                     else "immeuble" if "immeuble" in heading else "local_commercial")
    else:
        type_bien = "fonds_commerce"
    if rubric == "hotellerie" and "murs" in nature_lower and "fonds" not in nature_lower:
        type_bien = "local_commercial"
    details = {key: value for key, value in details.items() if value not in (None, "")}
    images = list(dict.fromkeys(re.findall(r"https://files\.msimond\.fr/PJ/[^\"'\s]+\.(?:jpe?g|png|webp)", html[:html.find("À voir aussi") if "À voir aussi" in html else None], re.I)))
    return {
        "id": offer.group(1) if offer else slug, "name": _text(title.group(1)),
        "price": price, "surface": surface, "land_surface": None, "rooms": None,
        "zipcode": postal.group(1) if postal else None, "city": city,
        "lat": None, "lng": None, "type_hint": f"vente {type_bien}", "url": url,
        "body": _text(body.group(1)) if body else None, "seller_name": "Michel Simond",
        "image_count": len(images), "published_at": None,
        "details_json": json.dumps(details, ensure_ascii=False),
        "images_json": json.dumps(images, ensure_ascii=False),
        "reference_annonce": reference.group(1).strip() if reference else None,
        "raw_payload": json.dumps({"prices": prices, "summary": lines, "offer_ref": offer_ref, "city": city_text},
                                  ensure_ascii=False),
    }


register(MichelSimondConnector())
