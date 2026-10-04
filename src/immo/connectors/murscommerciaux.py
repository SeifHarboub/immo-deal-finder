"""MursCommerciaux.com : murs commerciaux à vendre, souvent loués avec loyer publié.

Pas de sitemap d'annonces : la liste paginée `toutes-les-annonces.php?&limit=N,8`
donne les fiches `/annonce/{id}/{slug}`. Le robots.txt demande `Crawl-delay: 1`
(délai minimal 1 s, un seul worker par défaut).
"""

from collections.abc import Iterator
from html import unescape
import json
import os
import re

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


BASE = "https://www.murscommerciaux.com"
LIST = BASE + "/toutes-les-annonces.php?&limit={offset},8"
_DETAIL = re.compile(r"https://www\.murscommerciaux\.com/annonce/(\d+)/[a-z0-9-]+")


def _text(html: str | None) -> str:
    text = re.sub(r"<br\s*/?>|</p>", "\n", html or "", flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", " ", text)).replace("\xa0", " ").replace(" ", " ")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def _num(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"\d[\d\s.]*(?:,\d+)?", value)
    if not match:
        return None
    raw = match.group(0).strip()
    raw = re.sub(r"\s", "", raw)
    raw = raw.replace(".", "") if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", raw) else raw
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


class MursCommerciauxConnector(AgencyJsonLdConnector):
    PARSER_VERSION = 1

    def __init__(self) -> None:
        super().__init__("murscommerciaux", LIST.format(offset=0), lambda url: bool(_DETAIL.match(url)))
        for key, value in (("SCRAPE_MIN_DELAY_MURSCOMMERCIAUX", "1.0"),
                           ("SCRAPE_MAX_DELAY_MURSCOMMERCIAUX", "1.5"),
                           ("AGENCY_FETCH_WORKERS_MURSCOMMERCIAUX", "1")):
            os.environ.setdefault(key, value)

    def _sitemap_urls(self, url: str, seen=None) -> Iterator[str]:
        emitted: set[str] = set()
        offset, empty = 0, 0
        max_pages = int(os.getenv("MURSCOMMERCIAUX_MAX_LIST_PAGES", "200"))
        for _ in range(max_pages):
            result = self._fetch_detail(LIST.format(offset=offset))
            if result is None:
                empty += 1
                if empty >= 2:
                    break
                offset += 8
                continue
            links = [match.group(0) for match in _DETAIL.finditer(result[0])]
            fresh = [link for link in dict.fromkeys(links) if link not in emitted]
            if not fresh:
                break
            for link in fresh:
                emitted.add(link)
                yield link
            offset += 8

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        record = parse_detail(html, page_url)
        if record:
            yield record


def parse_detail(html: str, page_url: str) -> dict | None:
    title = re.search(r'<h1 class="detail-title">(.*?)</h1>', html, re.S)
    ident = _DETAIL.match(page_url) or re.search(r"details-annonce\.php\?id=(\d+)", html)
    if not title or not ident:
        return None
    external_id = ident.group(1)
    url = page_url if _DETAIL.match(page_url) else f"{BASE}/annonce/{external_id}"
    labels = {
        _text(label): _text(value) for label, value in re.findall(
            r'class="(?:characteristic|detail-stat)-label">(.*?)</span>\s*<span class="(?:characteristic|detail-stat)-value">(.*?)</span>',
            html, re.S,
        )
    }
    labels = {key: value for key, value in labels.items() if value and value not in ("—", "-")}
    price_block = re.search(r'<div class="price-value">(.*?)</div>', html, re.S)
    price = _num(_text(price_block.group(1))) if price_block else None
    reference = re.search(r"Référence :\s*<strong>([^<]+)</strong>", html)
    badge = re.search(r'<span class="detail-badge-text">(.*?)</span>\s*</div>', html, re.S)
    badge_text = _text(badge.group(1)) if badge else ""
    location = re.search(r'<div class="detail-location">(.*?)</div>', html, re.S)
    location_text = _text(location.group(1)) if location else ""
    postal = re.search(r"\b(\d{5})\b", f"{labels.get('Ville', '')} {location_text}")
    city = re.sub(r"\s*\(.*$|\s*-\s*\d{5}.*$", "", labels.get("Ville") or location_text).strip() or None
    description = re.search(r'<h2 class="section-title">Description</h2>\s*<div class="section-text">(.*?)</div>', html, re.S)
    details: dict = {"Statut": badge_text.split(" SITU")[0].strip().capitalize() or None}
    rent_text = labels.get("Loyer")
    if rent_text and _num(rent_text):
        rent = _num(rent_text)
        details["Loyer annuel"] = rent * 12 if re.search(r"/\s*mois", rent_text) else rent
    rate = labels.get("Rentabilité brute") or labels.get("Rentabilité")
    if rate and _num(rate):
        details["Rentabilité brute"] = _num(rate)
    for key in ("Type de mur", "Occupant / locataire", "Étages et niveaux", "Type de bail", "Environnement commercial",
                "Indexation du loyer", "Charges trimestrielles", "Dépôt de garantie", "Valeur locative",
                "Surface rez-de-chaussée", "Surface sous-sol", "Surface étage", "Date de début du bail",
                "Date de fin du bail", "Durée du bail"):
        if labels.get(key):
            details[key] = labels[key]
    for key, value in labels.items():  # dates de bail éventuelles sous d'autres libellés
        if "bail" in key.lower() and key not in details:
            details[key] = value
    tax = labels.get("Taxe foncière")
    if tax and _num(tax):
        details["Taxe foncière"] = _num(tax)
    elif tax:
        details["Taxe foncière à la charge"] = tax.replace("€", "").strip()
    occupied = "occup" in badge_text.lower() or bool(labels.get("Occupant / locataire")) or "Loyer annuel" in details
    if "libre" in badge_text.lower() and not labels.get("Occupant / locataire"):
        occupied = False
    details["Occupé"] = "oui" if occupied else "non"
    kind = f"{labels.get('Type de mur', '')} {badge_text} {_text(title.group(1))}".lower()
    type_bien = ("immeuble" if "immeuble" in kind else "bureau" if "bureau" in kind
                 else "terrain" if "terrain" in kind else "local_commercial")
    surface = _num(labels.get("Surface"))
    images = list(dict.fromkeys(
        BASE + "/admin/photos/" + name for name in re.findall(r"/admin/photos/([^\"'\s)]+_" + external_id + r";[^\"'\s)]+)", html)
    ))
    details = {key: value for key, value in details.items() if value not in (None, "")}
    return {
        "id": external_id, "name": _text(title.group(1)), "price": price, "surface": surface,
        "land_surface": None, "rooms": None, "zipcode": postal.group(1) if postal else None,
        "city": city.title() if city and city.isupper() else city, "lat": None, "lng": None,
        "type_hint": f"vente {type_bien}", "url": url,
        "body": _text(description.group(1)) if description else None, "seller_name": None,
        "image_count": len(images), "published_at": None,
        "details_json": json.dumps(details, ensure_ascii=False),
        "images_json": json.dumps(images, ensure_ascii=False),
        "reference_annonce": reference.group(1).strip() if reference else None,
        "raw_payload": json.dumps({"labels": labels, "badge": badge_text}, ensure_ascii=False),
    }


register(MursCommerciauxConnector())
