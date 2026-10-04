"""Bien'ici via son API JSON publique de recherche.

Le robots.txt interdit les pages HTML ``/annonces-*`` : seules l'API
``realEstateAds.json`` et l'autocomplétion ``suggest.json`` sont utilisées.
Une recherche est plafonnée à from+size <= 2500 : la couverture nationale
passe donc par un découpage département puis tranches de prix.
"""
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from html import unescape
import json
import os
from pathlib import Path
import random
import re
import time

import requests

from immo.connectors.agency import USER_AGENT
from immo.connectors.base import Connector, register
from immo.schema import Criteria


API_URL = "https://www.bienici.com/realEstateAds.json"
SUGGEST_URL = "https://res.bienici.com/suggest.json"
ZONES_PATH = Path(__file__).resolve().parents[3] / "config" / "bienici-zones.json"
MAX_WINDOW = 2500
PROPERTY_TYPES = [
    "house", "flat", "building", "parking", "loft", "castle", "townhouse",
    "shop", "office", "premises", "land",
]
DEPARTMENTS = (
    [f"{value:02d}" for value in range(1, 20)] + ["2A", "2B"]
    + [f"{value:02d}" for value in range(21, 96)]
    + ["971", "972", "973", "974", "976"]
)


class CircuitOpen(RuntimeError):
    pass


class PoliteClient:
    """Client HTTP JSON : délai entre requêtes, backoff 429/5xx, coupe-circuit.

    Le délai se règle par SCRAPE_MIN_DELAY_<SOURCE>/SCRAPE_MAX_DELAY_<SOURCE> ;
    au-delà de N échecs consécutifs (SCRAPE_CIRCUIT_BREAKER_<SOURCE>) la
    source est arrêtée pour ce cycle au lieu d'insister.
    """

    def __init__(self, source: str, min_delay: float, max_delay: float,
                 headers: dict | None = None) -> None:
        suffix = source.upper().replace("-", "_")
        self.min_delay = float(os.getenv(f"SCRAPE_MIN_DELAY_{suffix}", min_delay))
        self.max_delay = max(self.min_delay, float(os.getenv(f"SCRAPE_MAX_DELAY_{suffix}", max_delay)))
        self.retries = max(0, int(os.getenv(f"SCRAPE_RETRIES_{suffix}", "4")))
        self.breaker = max(1, int(os.getenv(f"SCRAPE_CIRCUIT_BREAKER_{suffix}", "5")))
        self.timeout = float(os.getenv(f"SCRAPE_TIMEOUT_{suffix}", "45"))
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, **(headers or {})})
        self.requests = self.failures = self.consecutive_failures = 0
        self._last = 0.0

    def _wait(self) -> None:
        delay = random.uniform(self.min_delay, self.max_delay)
        remaining = self._last + delay - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        self._last = time.monotonic()

    def get_json(self, url: str, params: dict | None = None, allow_status: set[int] = frozenset()):
        """Retourne le JSON, None pour un statut toléré (404, 400 attendu)."""
        if self.consecutive_failures >= self.breaker:
            raise CircuitOpen(f"{self.consecutive_failures} échecs consécutifs")
        for attempt in range(self.retries + 1):
            self._wait()
            self.requests += 1
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                if response.status_code in {404, *allow_status}:
                    self.consecutive_failures = 0
                    return None
                if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
                    raise requests.HTTPError(f"HTTP {response.status_code}", response=response)
                response.raise_for_status()
                payload = response.json()
                self.consecutive_failures = 0
                return payload
            except (requests.RequestException, ValueError) as exc:
                self.failures += 1
                if attempt >= self.retries:
                    break
                retry_after = getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After", "")
                backoff = float(retry_after) if str(retry_after).isdigit() else 2 ** (attempt + 1)
                time.sleep(min(backoff, 120) + random.uniform(0, 1))
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.breaker:
            raise CircuitOpen(f"{self.consecutive_failures} échecs consécutifs")
        return None


def read_state(source: str, key: str) -> datetime | None:
    """État de collecte (dernier passage complet, filigrane) stocké dans
    connector_url_checks sous une URL sentinelle ``state:<clé>``."""
    try:
        from immo.warehouse import connect
        with connect() as con:
            row = con.execute(
                "SELECT max(last_checked) FROM connector_url_checks "
                "WHERE source=? AND url=? AND parser_version=0",
                [source, f"state:{key}"],
            ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def write_state(source: str, key: str, value: datetime) -> None:
    try:
        from immo.warehouse import connect
        with connect() as con:
            con.execute("""
                INSERT INTO connector_url_checks (source,url,parser_version,last_checked,status)
                VALUES (?,?,0,?,'state')
                ON CONFLICT (source,url,parser_version) DO UPDATE SET last_checked=excluded.last_checked
            """, [source, f"state:{key}", value.replace(tzinfo=None)])
    except Exception:
        pass


def parse_date(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    # Bien'ici masque certaines dates derrière l'époque Unix.
    return None if parsed.year < 1990 else parsed.astimezone(timezone.utc)


def clean_html(value) -> str | None:
    if not value:
        return None
    text = re.sub(r"<br\s*/?>", "\n", str(value), flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", " ", text)).replace("\xa0", " ")
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line) or None


def _yes(value) -> str | None:
    return "Oui" if value is True else ("Non" if value is False else None)


def build_zone_cache(path: Path = ZONES_PATH) -> dict[str, list[str]]:
    """Construit une fois le cache code département -> zoneIds Bien'ici."""
    client = PoliteClient("bienici", 1, 1.5)
    zones: dict[str, list[str]] = {}
    for code in DEPARTMENTS:
        entries = client.get_json(SUGGEST_URL, {"q": code}) or []
        # Rhône : l'alias couvre le département et la Métropole de Lyon.
        match = next((item for item in entries if item.get("type") == "alias-department"
                      and str(item.get("ref")) == code.lstrip("0")), None)
        match = match or next((item for item in entries if item.get("type") == "department"
                               and code in {str(item.get("insee_code")), str(item.get("ref"))}), None)
        if match and match.get("zoneIds"):
            zones[code] = [str(zone) for zone in match["zoneIds"]]
    path.write_text(json.dumps(zones, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return zones


def map_ad(ad: dict) -> dict:
    """Convertit une annonce de l'API de recherche dans le schéma Bronze commun."""
    blur = ad.get("blurInfo") or {}
    position = blur.get("position") or blur.get("centroid") or {}
    radius = blur.get("radius")
    district = ad.get("district") or {}
    photos = [photo.get("url") for photo in ad.get("photos") or [] if isinstance(photo, dict) and photo.get("url")]
    ad_type = (ad.get("adTypeFR") or "").lower()
    dpe = str(ad.get("energyClassification") or "").upper()
    ges = str(ad.get("greenhouseGazClassification") or "").upper()
    details = {
        "Code INSEE": district.get("insee_code") or district.get("code_insee"),
        "Quartier": district.get("libelle") or district.get("name"),
        "Nature de la vente": ad.get("adTypeFR"),
        "Prix hors honoraires": ad.get("priceWithoutFees"),
        "Honoraires (%)": ad.get("agencyFeePercentage"),
        "Honoraires à la charge": ad.get("feesChargedTo"),
        "Prix au m² annoncé": ad.get("pricePerSquareMeter"),
        "Baisse de prix": "Oui" if ad.get("priceHasDecreased") else None,
        "Année de construction": ad.get("yearOfConstruction"),
        "Travaux nécessaires": "Oui" if ad.get("workToDo") else None,
        "Rénové": "Oui" if ad.get("isRefurbished") else None,
        "Étage": ad.get("floor"), "Nombre d'étages": ad.get("floorQuantity"),
        "Ascenseur": _yes(ad.get("hasElevator")), "Cave": _yes(ad.get("hasCellar")),
        "Parking(s)": ad.get("parkingPlacesQuantity"), "Garage(s)": ad.get("garagesQuantity"),
        "Piscine": "Oui" if ad.get("hasPool") else None,
        "Jardin": "Oui" if ad.get("hasGarden") else None,
        "Terrasse": "Oui" if ad.get("hasTerrace") else None,
        "Balcon(s)": ad.get("balconyQuantity"),
        "Salles de bain": ad.get("bathroomsQuantity"), "Salles d'eau": ad.get("showerRoomsQuantity"),
        "Chauffage": ad.get("heating"), "Exposition": ad.get("exposition"),
        "DPE consommation": ad.get("energyValue"), "GES émissions": ad.get("greenhouseGazValue"),
        "Date du DPE": ad.get("energyPerformanceDiagnosticDate"),
        "Coût énergétique annuel min": ad.get("minEnergyConsumption"),
        "Coût énergétique annuel max": ad.get("maxEnergyConsumption"),
        "Copropriété": "Oui" if ad.get("isInCondominium") else None,
        "Nombre de lots": ad.get("condominiumPartsQuantity"),
        "Charges annuelles de copropriété": ad.get("annualCondominiumFees"),
        "Procédure de copropriété en cours": _yes(ad.get("isCondominiumInProcedure")),
        "Exclusivité": "Oui" if ad.get("isExclusiveSaleMandate") else None,
        "Type d'annonceur": ad.get("accountType"),
        "Annonce professionnelle": _yes(ad.get("adCreatedByPro")),
        "Date de modification": ad.get("modificationDate"),
        "Disponibilité": ad.get("availableDate"),
    }
    if ad.get("lifeAnnuityMonthlyAllowance") or ad.get("lifeAnnuityAgeOfMan") or ad.get("lifeAnnuityAgeOfWoman"):
        details.update({
            "Viager": "oui", "Rente mensuelle viagère": ad.get("lifeAnnuityMonthlyAllowance"),
            "Âge du crédirentier": ad.get("lifeAnnuityAgeOfMan"),
            "Âge de la crédirentière": ad.get("lifeAnnuityAgeOfWoman"),
        })
    # Champs financiers éventuels des locaux et fonds (rarement renseignés).
    for key, label, factor in (
        ("annualRent", "Loyer annuel", 1), ("rent", "Loyer annuel", 12),
        ("rentalYield", "Rentabilité brute", 1), ("turnover", "Chiffre d'affaires", 1),
        ("propertyTax", "Taxe foncière", 1),
    ):
        if isinstance(ad.get(key), (int, float)) and ad[key] > 0 and label not in details:
            details[label] = ad[key] * factor
    if blur.get("type") != "exact" and (radius is None or radius > 150):
        details["Précision cartographique"] = "approximative"
        details["Rayon de floutage (m)"] = radius
    details = {key: value for key, value in details.items() if value not in (None, "", [], {})}
    published = parse_date(ad.get("publicationDate"))
    return {
        "id": ad.get("id"), "name": ad.get("title"), "price": ad.get("price"),
        "surface": ad.get("surfaceArea"), "land_surface": ad.get("landSurfaceArea"),
        "rooms": ad.get("roomsQuantity"), "bedrooms": ad.get("bedroomsQuantity"),
        "zipcode": ad.get("postalCode"), "city": ad.get("city"),
        "lat": position.get("lat"), "lng": position.get("lon"),
        "type_hint": f"{ad.get('propertyType') or ''}/{ad_type}",
        "url": f"https://www.bienici.com/annonce/{ad.get('id')}",
        "body": clean_html(ad.get("description")),
        "seller_name": ad.get("accountDisplayName"), "seller_type": ad.get("accountType"),
        "dpe": dpe if re.fullmatch(r"[A-G]", dpe) else None,
        "ges": ges if re.fullmatch(r"[A-G]", ges) else None,
        "image_count": len(photos), "published_at": published.isoformat() if published else None,
        "details_json": json.dumps(details, ensure_ascii=False),
        "images_json": json.dumps(photos, ensure_ascii=False),
        "reference_annonce": ad.get("reference"),
        "raw_payload": json.dumps(ad, ensure_ascii=False, default=str),
    }


class BienIciConnector(Connector):
    source = "bienici"

    def __init__(self) -> None:
        self.client: PoliteClient | None = None
        self.pages = self.missed = 0
        self.max_pages = 0

    def _filters(self, **extra) -> dict:
        return {
            "filterType": "buy", "propertyType": PROPERTY_TYPES,
            "onTheMarket": [True], "newProperty": False, **extra,
        }

    def _search(self, filters: dict) -> dict | None:
        assert self.client is not None
        payload = self.client.get_json(
            API_URL, {"filters": json.dumps(filters, separators=(",", ":"))}, allow_status={400},
        )
        if payload is None:
            self.missed += 1
        return payload

    def _zones(self) -> dict[str, list[str]]:
        if ZONES_PATH.exists():
            return json.loads(ZONES_PATH.read_text(encoding="utf-8"))
        return build_zone_cache()

    def _budget_left(self) -> bool:
        return not self.max_pages or self.pages < self.max_pages

    def _slice(self, zone: list[str], low: int, high: int | None) -> Iterator[dict]:
        """Une zone et une tranche de prix ; découpe tant que total >= 2500."""
        price = {"minPrice": low, **({"maxPrice": high} if high is not None else {})}
        base = self._filters(zoneIdsByTypes={"zoneIds": zone}, sortBy="price", sortOrder="asc", **price)
        probe = self._search({**base, "size": 1, "from": 0, "page": 1})
        total = int((probe or {}).get("total") or 0)
        if total >= MAX_WINDOW and (high is None or high > low):
            middle = (low + high) // 2 if high is not None else max(low * 2, low + 200_000)
            yield from self._slice(zone, low, middle)
            yield from self._slice(zone, middle + 1, high)
            return
        if total >= MAX_WINDOW:
            print(f"Bien'ici : tranche {zone} {low}-{high} plafonnée à {MAX_WINDOW}/{total}.", flush=True)
        size = self.page_size
        start = 0
        while start < min(total, MAX_WINDOW) and self._budget_left():
            payload = self._search({**base, "size": min(size, MAX_WINDOW - start), "from": start,
                                    "page": start // size + 1})
            self.pages += 1
            ads = (payload or {}).get("realEstateAds") or []
            yield from ads
            if not ads:
                break
            start += len(ads)

    def _full_scan(self) -> Iterator[dict]:
        for code, zone in self._zones().items():
            if not self._budget_left():
                return
            yield from self._slice(zone, 0, None)

    def _incremental(self, since: datetime) -> Iterator[dict]:
        """Dernières modifications (nouvelles annonces et baisses de prix),
        par département puisque 2 500 modifications couvrent ~1 h en national."""
        for code, zone in self._zones().items():
            base = self._filters(zoneIdsByTypes={"zoneIds": zone},
                                 sortBy="modificationDate", sortOrder="desc")
            start = 0
            while start < MAX_WINDOW and self._budget_left():
                size = min(self.incremental_size, MAX_WINDOW - start)
                payload = self._search({**base, "size": size, "from": start, "page": start // size + 1})
                self.pages += 1
                ads = (payload or {}).get("realEstateAds") or []
                for ad in ads:
                    modified = parse_date(ad.get("modificationDate"))
                    if modified and modified < since:
                        ads = []
                        break
                    yield ad
                if len(ads) < size:
                    break
                start += size

    def fetch(self, c: Criteria) -> Iterator[dict]:
        self.client = PoliteClient(self.source, 1, 1.5)
        self.pages = self.missed = 0
        self.max_pages = c.max_pages or 0
        self.page_size = max(10, min(500, int(os.getenv("BIENICI_PAGE_SIZE", "500"))))
        self.incremental_size = max(10, min(500, int(os.getenv("BIENICI_INCREMENTAL_PAGE_SIZE", "100"))))
        started = datetime.now(timezone.utc)
        full_days = float(os.getenv("BIENICI_FULL_SCAN_DAYS", "3"))
        last_full = read_state(self.source, "full_scan")
        watermark = read_state(self.source, "watermark")
        forced = os.getenv("BIENICI_MODE", "").lower()
        full = forced == "full" or (forced != "incremental" and (
            last_full is None or watermark is None
            or last_full.replace(tzinfo=timezone.utc) < started - timedelta(days=full_days)
        ))
        mode = "complet" if full else "incrémental"
        count = 0
        circuit = False
        try:
            stream = self._full_scan() if full else self._incremental(
                watermark.replace(tzinfo=timezone.utc) - timedelta(minutes=30)
            )
            for ad in stream:
                if not isinstance(ad, dict) or not ad.get("id"):
                    continue
                if ad.get("adType") not in (None, "buy"):
                    continue
                count += 1
                yield map_ad(ad)
        except CircuitOpen:
            circuit = True
        complete = not circuit and not self.max_pages and not self.missed
        if complete:
            write_state(self.source, "watermark", started)
            if full:
                write_state(self.source, "full_scan", started)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        print(
            f"Diagnostic {self.source}: mode {mode}, {self.client.requests} requêtes, "
            f"{self.pages} pages, {count} annonces, {self.client.failures} échecs, "
            f"{self.missed} réponses manquantes, {elapsed:.1f}s"
            f"{' (coupe-circuit ouvert)' if circuit else ''}"
            f"{'' if complete else ' (passage partiel, état non avancé)'}.",
            flush=True,
        )


register(BienIciConnector())
