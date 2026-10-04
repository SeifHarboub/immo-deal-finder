"""Place des Commerces : fonds de commerce et cessions de bail (France).

Pas de sitemap. Chaque passage lit d'abord les dernières annonces (pages 1..10),
puis avance le balayage complet `resultats_fr_?lieu=p33` de quelques pages,
la page suivante étant mémorisée dans un petit fichier d'état
(`PLACEDESCOMMERCES_STATE_FILE`, défaut `data/runtime/placedescommerces-sweep.json`).
Le loyer publié est payé par l'exploitant : il est rangé sous la clé non
standard « Loyer du bail annuel » pour ne pas passer pour un revenu.
"""

from collections.abc import Iterator
from datetime import datetime
from html import unescape
import json
import os
from pathlib import Path
import re

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


BASE = "https://www.placedescommerces.com/"
RECENT = BASE + "dernieres-annonces-commerces-a-vendre_fr_?page={page}"
SWEEP = BASE + "resultats_fr_?secteur=&lieu=p33&from=1&page={page}"
_LINK = re.compile(r'href="/?(vente-[a-z0-9-]+,c(\d+)_fr_)')
_MONTHS = {"janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
           "juillet": 7, "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
           "décembre": 12, "decembre": 12}


def _flat(html: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
    text = re.sub(r"<[^>]+>", "|", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"(\|\s*)+", "|", text)


def _text(html: str | None) -> str:
    text = re.sub(r"<br\s*/?>|</p>", "\n", html or "", flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", " ", text)).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def _num(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"-?\d[\d\s.]*(?:,\d+)?", value)
    if not match:
        return None
    raw = re.sub(r"[\s.]", "", match.group(0)).replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def canonical(path: str) -> str:
    return BASE + path.lstrip("/").split("?", 1)[0]


class PlaceDesCommercesConnector(AgencyJsonLdConnector):
    PARSER_VERSION = 1

    def __init__(self) -> None:
        super().__init__("placedescommerces", RECENT.format(page=1), lambda url: bool(_LINK.search(f'href="{url}')))
        for key, value in (("SCRAPE_MIN_DELAY_PLACEDESCOMMERCES", "0.5"),
                           ("SCRAPE_MAX_DELAY_PLACEDESCOMMERCES", "1.0"),
                           ("AGENCY_FETCH_WORKERS_PLACEDESCOMMERCES", "4")):
            os.environ.setdefault(key, value)

    # --- état du balayage progressif -------------------------------------
    @staticmethod
    def state_path() -> Path:
        return Path(os.getenv("PLACEDESCOMMERCES_STATE_FILE", "data/runtime/placedescommerces-sweep.json"))

    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"next_page": 1, "last_page": None}

    def _save_state(self, state: dict) -> None:
        path = self.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(state), encoding="utf-8")
        temp.replace(path)

    def _list_links(self, url: str) -> tuple[list[str], int | None] | None:
        result = self._fetch_detail(url)  # délai, reprises et backoff du socle
        if result is None:
            return None
        html = result[0]
        links = list(dict.fromkeys(canonical(match.group(1)) for match in _LINK.finditer(html)))
        pages = [int(value) for value in re.findall(r"[?&]page=(\d+)", html)]
        return links, max(pages) if pages else None

    def _sitemap_urls(self, url: str, seen=None) -> Iterator[str]:
        emitted: set[str] = set()
        recent_pages = int(os.getenv("PLACEDESCOMMERCES_RECENT_PAGES", "10"))
        for page in range(1, recent_pages + 1):
            result = self._list_links(RECENT.format(page=page))
            if not result or not result[0]:
                break
            for link in result[0]:
                if link not in emitted:
                    emitted.add(link)
                    yield link
        budget = int(os.getenv("PLACEDESCOMMERCES_SWEEP_PAGES_PER_RUN", "120"))
        state = self._load_state()
        failures = 0
        for _ in range(budget):
            page = int(state.get("next_page") or 1)
            result = self._list_links(SWEEP.format(page=page))
            if result is None:
                failures += 1
                if failures >= 3:
                    break
                continue
            links, last_page = result
            if last_page:
                state["last_page"] = max(last_page, page)
            # Fin du catalogue (page vide ou au-delà de la dernière) : on reboucle.
            done = not links or (state.get("last_page") and page >= state["last_page"])
            state["next_page"] = 1 if done else page + 1
            state["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._save_state(state)
            for link in links:
                if link not in emitted:
                    emitted.add(link)
                    yield link
            if done:
                break

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        record = parse_detail(html, page_url)
        if record:
            yield record


def _financials(html: str) -> dict:
    """Tableau « Éléments chiffrés » : dernière année renseignée pour chaque ligne."""
    table = re.search(r"Éléments chiffrés.*?<table[^>]*>(.*?)</table>", html, re.S)
    if not table:
        return {}
    years = re.findall(r"<b>\s*(\d{4})\s*</b>", table.group(1))
    out: dict = {}
    names = {"CA": "Chiffre d'affaires", "EBE": "EBE", "Res. net": "Résultat net",
             "Marge brute": "Marge brute", "Res. exploit.": "Résultat d'exploitation", "Nb pers.": "Effectif"}
    for row in re.findall(r"<tr>(.*?)</tr>", table.group(1) + "</tr>", re.S):
        cells = [_text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 2 or cells[0] not in names:
            continue
        for index in range(len(cells) - 1, 0, -1):
            value = _num(cells[index])
            if value is not None and cells[index].strip():
                out[names[cells[0]]] = value
                if cells[0] == "CA" and index - 1 < len(years):
                    out["Année des chiffres"] = int(years[index - 1])
                break
    if out.get("EBE") is not None and out["EBE"] <= 0:
        out["EBE négatif"] = out.pop("EBE")
    if out.get("Résultat net") is not None and out["Résultat net"] <= 0:
        out["Résultat net négatif"] = out.pop("Résultat net")
    return out


def parse_detail(html: str, page_url: str) -> dict | None:
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    canon = re.search(r'<link rel="canonical" href="([^"]+)"', html)
    url = canonical((canon.group(1) if canon else page_url).replace(BASE, ""))
    ident = re.search(r",c(\d+)_fr_", url)
    title = re.search(r'<h2 class="annonce">(.*?)</h2>', html, re.S)
    if not ident or not title:
        return None
    name = _text(title.group(1))
    start = html.find('<h2 class="annonce">')
    end = html.find("Contacter le d", start)
    block = html[start:end if end > 0 else None]
    flat = _flat(block)

    def field(label: str) -> str | None:
        match = re.search(rf"\|{label}\s*:?\s*\|([^|]+)", flat)
        return match.group(1).strip() if match else None

    price = _num(field("Prix"))
    reference = field(r"Réf\.")
    published = None
    date = re.search(r"(?:Posté|Mise à jour) le \|(\d{1,2}) (\w+) (\d{4})", flat)
    if date and date.group(2).lower() in _MONTHS:
        published = datetime(int(date.group(3)), _MONTHS[date.group(2).lower()], int(date.group(1))).date().isoformat()
    location = re.search(r"\|Localisation : \|([^|]+)(?:\|/ \|([^|]+))?(?:\|/ \|([^|]+))?", flat)
    region, dept, city = (location.groups() if location else (None, None, None))
    sector = re.search(r"\|Secteur :\s*\|(.+?)\|(?:\d+\|)?Description de l", flat)
    sector_text = re.sub(r"\|/ \|", " / ", sector.group(1)).strip("| ") if sector else None
    postal = re.search(r"\((\d{5})\)", name)
    description = re.search(r'<div class="col-12 col-sm-10 offset-sm-1 mt-5">(.*?)</div>', block, re.S)
    strengths = re.search(r"Points fort\s*:\s*</strong>.*?<p>(.*?)</p>", block, re.S)
    body = "\n".join(filter(None, [
        _text(description.group(1)) if description else None,
        ("Points forts :\n" + _text(strengths.group(1))) if strengths else None,
    ])) or None
    details: dict = {
        "Secteur": sector_text, "Région": region, "Département": dept,
        "Surface commerciale": _num(field("Surface commerciale")),
        "Surface totale": _num(field("Surface totale")),
        "Places en salle": _num(field("Nombre de places en salle")),
        "Places en terrasse": _num(field("Nombre de places en terrasse")),
        "Logement de fonction": field("Logement de fonction présent"),
    }
    if re.search(r"licence IV", flat, re.I):
        details["Licence IV"] = "oui"
    rent = re.search(r"\|Loyer mensuel : \|([^|]+)\|([^|]*)", flat)
    if rent and _num(rent.group(1)):
        monthly = _num(rent.group(1))
        details["Loyer mensuel du bail"] = monthly
        details["Loyer du bail annuel"] = round(monthly * 12, 2)
        details["Conditions du loyer"] = rent.group(2).strip() or None
    details.update(_financials(block))
    details = {key: value for key, value in details.items() if value not in (None, "")}
    lower = f"{sector_text or ''} {name}".lower()
    type_bien = "local_commercial" if re.search(r"\bmurs\b", lower) and "fonds" not in lower else "fonds_commerce"
    photos = list(dict.fromkeys(re.findall(r"https://photos\.placedescommerces\.com/photos_annonces/\d+/\d+\.jpg", block)))
    count = re.search(r'font-size: 22px;">(\d+)</div>', block)
    surface = details.get("Surface totale") or details.get("Surface commerciale")
    return {
        "id": ident.group(1), "name": name, "price": price, "surface": surface,
        "land_surface": None, "rooms": None,
        "zipcode": postal.group(1) if postal else None, "city": city.strip() if city else None,
        "lat": None, "lng": None, "type_hint": f"vente {type_bien}", "url": url,
        "body": body, "seller_name": None,
        "image_count": int(count.group(1)) if count else len(photos), "published_at": published,
        "details_json": json.dumps(details, ensure_ascii=False),
        "images_json": json.dumps(photos, ensure_ascii=False),
        "reference_annonce": reference,
        "raw_payload": json.dumps({"flat": flat[:6000]}, ensure_ascii=False),
    }


register(PlaceDesCommercesConnector())
