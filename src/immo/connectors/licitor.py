"""Licitor : ventes judiciaires nationales, et socle commun des connecteurs d'enchères.

Le socle (`EnchereConnector` et les fonctions utilitaires) est importé par les
connecteurs avoventes, encheresimmo, agorastore, heures36 et vench. Il impose une
politesse stricte : robots.txt respecté, au moins 1 s entre deux requêtes vers un
même site quel que soit le nombre de workers (3 au plus), reprise exponentielle et
coupe-circuit. Les ventes passées sont conservées : elles calibrent l'écart entre
mise à prix et adjudication.
"""

from collections.abc import Iterator
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import random
import re
import threading
import time
import unicodedata
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import requests

from immo.connectors.agency import USER_AGENT, AgencyJsonLdConnector
from immo.connectors.base import register
from immo.schema import Criteria


# ---------------------------------------------------------------- utilitaires

MOIS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
}
STATUTS_FINAUX = {"adjugé", "carence", "retiré", "non communiqué"}
TYPES_BIEN = {"appartement", "maison", "immeuble", "terrain", "local_commercial",
              "fonds_commerce", "bureau", "autre"}
MODES_VENTE = {"enchere_judiciaire", "enchere_notariale", "vente_interactive",
               "cession_publique", "gre_a_gre"}
_NOMBRES = {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6,
            "sept": 7, "huit": 8, "neuf": 9, "dix": 10}


def plain(value) -> str:
    """Minuscules sans accents, espaces insécables normalisés."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[\s  ]+", " ", text.lower().replace("’", "'")).strip()


def slug(value) -> str:
    return re.sub(r"[^a-z0-9]+", "-", plain(value)).strip("-")


class _TextParser(HTMLParser):
    """Extraction de texte tolérante (attributs contenant '>' ou guillemets isolés)."""

    BLOCKS = {"br", "p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "ul"}
    SKIP = {"script", "style", "svg", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs) -> None:
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")
        else:
            self.parts.append(" ")

    def handle_endtag(self, tag) -> None:
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data) -> None:
        if not self.skip:
            self.parts.append(data)


def clean_text(fragment) -> str:
    """HTML → texte lisible (sauts de ligne conservés)."""
    if not fragment:
        return ""
    parser = _TextParser()
    parser.feed(str(fragment))
    parser.close()
    lines = [re.sub(r"[ \t\u00a0\u202f]+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def parse_amount(value) -> float | None:
    """'400 000 €', '48 500,00 €', '20.000 €', '11 587.00 €' → float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d[\d   .,]*", str(value))
    if not match:
        return None
    raw = re.sub(r"[   ]", "", match.group(0)).rstrip(".,")
    if "," in raw and "." in raw:
        decimal = "," if raw.rfind(",") > raw.rfind(".") else "."
        raw = raw.replace("." if decimal == "," else ",", "").replace(decimal, ".")
    elif "," in raw:
        raw = raw.replace(",", ".") if re.search(r",\d{1,2}$", raw) else raw.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_surface(text) -> float | None:
    match = re.search(r"(\d[\d   .,]*)\s*m(?:²|2|\s?carr)", str(text or ""))
    value = parse_amount(match.group(1)) if match else None
    return value if value and 1 <= value < 1_000_000 else None


def parse_contenance(text) -> float | None:
    """Contenance cadastrale '1 ha 06 a 95 ca' ou 'terrain de 500 m²' → m²."""
    source = plain(text)
    match = re.search(r"(?:(\d+)\s*ha\s*)?(?:(\d+)\s*a\s+)?(\d+)\s*ca\b", source)
    if match and (match.group(1) or match.group(2)):
        return float(int(match.group(1) or 0) * 10000 + int(match.group(2) or 0) * 100 + int(match.group(3)))
    match = re.search(r"(?:terrain|parcelle|jardin)[^.;\n]{0,60}?(\d[\d .,]*)\s*m(?:²|2)", source)
    return parse_amount(match.group(1)) if match else None


def land_surface(kind: str, text, surface=None) -> float | None:
    """Terrain propre au bien : jamais la contenance d'une copropriété."""
    if kind == "terrain":
        return surface or parse_contenance(text)
    return parse_contenance(text) if kind in {"maison", "immeuble"} else None


def parse_rooms(text) -> int | None:
    match = re.search(r"\b(\d{1,2}|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix)\s+pieces?\b",
                      plain(text))
    if not match:
        match = re.search(r"\b(?:[tf]|type\s*)(\d{1,2})\b", plain(text))
    if not match:
        return None
    value = match.group(1)
    return int(value) if value.isdigit() else _NOMBRES.get(value)


def parse_fr_date(text) -> str | None:
    """'jeudi 5 novembre 2026 à 14h', '23/09/2026 à 09:30' → ISO local naïf."""
    source = plain(text)
    match = re.search(
        r"(\d{1,2})(?:er)?\s+(" + "|".join(MOIS) + r")\s+(\d{4})"
        r"(?:\s*(?:a|,)?\s*(\d{1,2})\s*(?:h|:)\s*(\d{2})?)?", source)
    if match:
        day, month, year = int(match.group(1)), MOIS[match.group(2)], int(match.group(3))
    else:
        match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\s*(?:a|,)?\s*(\d{1,2})\s*(?:h|:)\s*(\d{2})?)?",
                          source)
        if not match:
            return None
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    hour, minute = int(match.group(4) or 0), int(match.group(5) or 0)
    try:
        return datetime(year, month, day, min(hour, 23), min(minute, 59)).isoformat()
    except ValueError:
        return None


def iso_local(value) -> str | None:
    """'2026-11-19T09:00:00.000Z' ou '+02:00' → ISO local naïf (heure affichée)."""
    if not value:
        return None
    match = re.match(r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}(?::\d{2})?))?", str(value).lstrip("$D"))
    if not match:
        return None
    clock = match.group(2) or "00:00:00"
    return f"{match.group(1)}T{clock if len(clock) == 8 else clock + ':00'}"


def occupation(text) -> str | None:
    source = plain(text)
    if re.search(r"libres? de toute (?:occupation|location)|inoccupe|non occupe|\bvacant|\bvide de tout", source):
        return "libre"
    if re.search(r"\boccupee?s?\b|\bloue(?:e|s)?\b|bail (?:commercial|d'habitation|en cours)|\blocataire", source):
        return "occupé"
    if re.search(r"\blibres?\b", source):
        return "libre"
    return None


_TYPE_PATTERNS = [
    (r"fonds? de commerce|droit au bail", "fonds_commerce"),
    (r"local (?:a usage )?d'habitation|chambre de service|\bchambres? independante", "appartement"),
    (r"(?:batiment|immeuble|ensemble immobilier) a usage (?:commercial|industriel|professionnel|de bureaux?)",
     "local_commercial"),
    (r"\bappartements?\b|\bstudios?\b|\bduplex\b|\btriplex\b|\blogements?\b|\bt[1-6]\b|\bf[1-6]\b", "appartement"),
    (r"\bmaisons?\b|\bpavillons?\b|\bvillas?\b|\bproprietes?\b|\bfermes?\b|\blongeres?\b|\bmas\b"
     r"|\bchalets?\b|\bbastides?\b|\bmoulin\b|\bchateau\b", "maison"),
    (r"\bimmeubles?\b|\bbatiments?\b|ensemble immobilier", "immeuble"),
    (r"\bterrains?\b|\bparcelles?\b|\bbois\b|\bvignes?\b|\bpres?\b|\bterres\b|\bforet\b", "terrain"),
    (r"\bbureaux?\b|\bplateau de bureaux", "bureau"),
    (r"\blocal\b|\blocaux\b|\bcommerces?\b|\bboutiques?\b|\bentrepots?\b|\bhangars?\b|\bateliers?\b"
     r"|\bhotel\b|\brestaurant\b|murs commerciaux|\bcommercial\b|\bindustriel", "local_commercial"),
    (r"\bparkings?\b|\bbox\b|\bgarages?\b|\bcaves?\b|\bemplacements?\b|\bstationnement", "autre"),
]


def type_from_text(text) -> str:
    """Type canonique d'après le premier mot significatif du libellé."""
    source = plain(text)
    best = None
    for pattern, kind in _TYPE_PATTERNS:
        match = re.search(pattern, source)
        if match and (best is None or (match.start(), -len(match.group(0))) < best[0]):
            best = ((match.start(), -len(match.group(0))), kind)
    return best[1] if best else "autre"


def postcode_from_city(city) -> str | None:
    """Arrondissements Paris/Lyon/Marseille → code postal."""
    match = re.match(r"(paris|lyon|marseille)\s*(\d{1,2})\s*(?:er|e|eme)?\b", plain(city))
    if not match:
        return None
    number = int(match.group(2))
    prefix = {"paris": "750", "lyon": "6900", "marseille": "130"}[match.group(1)]
    return f"{prefix}{number:02d}" if match.group(1) != "lyon" else f"{prefix}{number}"


def city_key(city) -> str:
    text = re.sub(r"\b\d+\s*(?:er|e|eme)?\b|\bcedex\b|\barrondissement\b", " ", plain(city))
    text = re.sub(r"\bste?\b", lambda match: "sainte" if match.group(0) == "ste" else "saint", text)
    return slug(text)


def cle_enchere(tribunal, date_vente, ville, mise_a_prix) -> str | None:
    """Clé de rapprochement inter-sources : tribunal|date|ville|mise à prix."""
    if not date_vente or not mise_a_prix:
        return None
    court = re.sub(r"\(.*?\)", " ", plain(tribunal)).split(",")[0].strip()
    court = re.sub(r"^(?:tribunal (?:judiciaire|de grande instance|de commerce)|tj|tgi)\s*(?:de |d'|du )?",
                   "", court).strip()
    return "|".join([city_key(court) or "?", str(date_vente)[:10], city_key(ville) or "?",
                     str(int(round(float(mise_a_prix))))])


def make_record(**values) -> dict:
    """Enregistrement Bronze complet (colonnes communes + vente particulière)."""
    details = {key: value for key, value in (values.pop("details", None) or {}).items()
               if value not in (None, "", [], {})}
    images = values.pop("images", None) or []
    record = {key: None for key in (
        "id", "name", "price", "surface", "land_surface", "rooms", "bedrooms", "zipcode",
        "city", "lat", "lng", "type_hint", "url", "body", "seller_name", "dpe", "ges",
        "published_at", "reference_annonce", "raw_payload", "mode_vente", "date_vente", "prix_adjuge",
    )}
    record.update(values)
    for key in ("rooms", "bedrooms"):
        if isinstance(record[key], float) and record[key].is_integer():
            record[key] = int(record[key])
    for key in ("price", "surface", "land_surface", "rooms", "bedrooms", "lat", "lng", "prix_adjuge"):
        if record[key] is not None:
            record[key] = str(record[key])
    record["id"] = str(record["id"]) if record["id"] is not None else None
    record["details_json"] = json.dumps(details, ensure_ascii=False, default=str)
    record["images_json"] = json.dumps(images, ensure_ascii=False)
    record["image_count"] = len(images)
    return record


def round_robin(*streams) -> Iterator:
    """Alterne les flux pour qu'une limite par cycle profite à chacun."""
    active = [iter(stream) for stream in streams]
    while active:
        still = []
        for stream in active:
            try:
                yield next(stream)
                still.append(stream)
            except StopIteration:
                pass
        active = still


# ---------------------------------------------------------------- politesse

class _Robots:
    """robots.txt minimal avec jokers `*` et `$` (groupe User-agent: *)."""

    def __init__(self, text: str | None) -> None:
        self.rules: list[tuple[int, bool, re.Pattern]] = []
        if not text:
            return
        agents: list[str] = []
        in_rules = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            field, value = (part.strip() for part in line.split(":", 1))
            field = field.lower()
            if field == "user-agent":
                if in_rules:
                    agents, in_rules = [], False
                agents.append(value)
            elif field in {"allow", "disallow"}:
                in_rules = True
                if "*" in agents and value:
                    pattern = re.escape(value).replace(r"\*", ".*")
                    if pattern.endswith(r"\$"):
                        pattern = pattern[:-2] + "$"
                    self.rules.append((len(value), field == "allow", re.compile(pattern)))

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best = None
        for length, allow, pattern in self.rules:
            if pattern.match(path) and (best is None or (length, allow) > best):
                best = (length, allow)
        return True if best is None else best[1]


_HOST_LOCKS: dict[str, threading.Lock] = {}
_HOST_LAST: dict[str, float] = {}
_ROBOTS: dict[str, _Robots] = {}
_GLOBAL_LOCK = threading.Lock()


class EnchereConnector(AgencyJsonLdConnector):
    """Socle des connecteurs d'enchères : collecte polie, reprise et état persistant."""

    PARSER_VERSION = 1
    MODE_VENTE = "enchere_judiciaire"

    def __init__(self, source: str, start_url: str, url_filter=None) -> None:
        super().__init__(source, start_url, url_filter)
        self.session.headers.update({"Accept-Language": "fr-FR,fr;q=0.9"})
        self._final: set[str] = set()
        self._known: set[str] = set()
        self._state: dict = {}
        self._failures = 0
        self._stats_lock = threading.Lock()

    # -- configuration
    @property
    def _suffix(self) -> str:
        return self.source.upper().replace("-", "_")

    def _env(self, name: str, default) -> str:
        return os.getenv(f"{name}_{self._suffix}", os.getenv(name, str(default)))

    def _detail_session(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"})
            self._thread_local.session = session
        return session

    # -- politesse
    def _wait_turn(self, host: str) -> None:
        interval = max(1.0, float(os.getenv(f"SCRAPE_MIN_INTERVAL_{self._suffix}", "1.2")))
        with _GLOBAL_LOCK:
            lock = _HOST_LOCKS.setdefault(host, threading.Lock())
        with lock:
            delay = _HOST_LAST.get(host, 0.0) + interval + random.uniform(0, 0.3) - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            _HOST_LAST[host] = time.monotonic()

    def _robots(self, url: str) -> _Robots:
        parts = urlsplit(url)
        host = parts.netloc
        with _GLOBAL_LOCK:
            cached = _ROBOTS.get(host)
        if cached is not None:
            return cached
        text = None
        try:
            self._wait_turn(host)
            response = self._detail_session().get(f"{parts.scheme}://{host}/robots.txt", timeout=20)
            if response.status_code == 200:
                text = response.text
        except requests.RequestException:
            text = None
        rules = _Robots(text)
        with _GLOBAL_LOCK:
            _ROBOTS[host] = rules
        return rules

    def _get(self, url: str, timeout: float | None = None) -> tuple[str, str] | None:
        """GET poli : robots.txt, cadence par site, reprise exponentielle."""
        if not self._robots(url).allowed(url):
            return None
        retries = max(0, int(self._env("AGENCY_RETRIES", 2)))
        timeout = timeout or float(self._env("AGENCY_TIMEOUT", 30))
        host = urlsplit(url).netloc
        for attempt in range(retries + 1):
            self._wait_turn(host)
            try:
                response = self._detail_session().get(url, timeout=timeout)
            except requests.RequestException:
                response = None
            if response is not None and response.status_code in {404, 410}:
                return None
            if response is not None and response.ok:
                with self._stats_lock:
                    self._failures = 0
                response.encoding = response.encoding if "charset" in response.headers.get(
                    "content-type", "").lower() else "utf-8"
                return response.text, response.url
            with self._stats_lock:
                self._failures += 1
            if attempt < retries:
                retry_after = response.headers.get("Retry-After", "") if response is not None else ""
                delay = float(retry_after) if retry_after.isdigit() else 3 * 2 ** attempt
                time.sleep(min(delay, 60))
        return None

    def _fetch_detail(self, url: str) -> tuple[str, str] | None:
        return self._get(url)

    def _listing_open(self) -> bool:
        """Coupe-circuit des pages de liste : trop d'échecs consécutifs → arrêt."""
        return self._failures < max(1, int(self._env("AGENCY_CIRCUIT_BREAKER_LISTING", 6)))

    def _sitemap_urls(self, url: str, seen: set[str] | None = None) -> Iterator[str]:
        seen = seen if seen is not None else set()
        if url in seen or not self._listing_open():
            return
        seen.add(url)
        result = self._get(url, timeout=60)
        if not result:
            return
        try:
            root = ElementTree.fromstring(result[0].encode("utf-8").lstrip(b"\xef\xbb\xbf").strip())
        except ElementTree.ParseError:
            return
        locations = [node.text.strip() for node in root.iter() if node.tag.endswith("loc") and node.text]
        if root.tag.endswith("sitemapindex"):
            for child in locations:
                yield from self._sitemap_urls(urljoin(url, child), seen)
        else:
            yield from (item for item in locations if self.url_filter(item))

    # -- état persistant (curseur de rattrapage de l'historique)
    def _state_path(self) -> Path:
        return Path(os.getenv("ENCHERES_STATE_DIR", "data/encheres-state")).expanduser() / f"{self.source}.json"

    def _load_state(self) -> dict:
        try:
            return json.loads(self._state_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self, state: dict) -> None:
        path = self._state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
            temp.replace(path)
        except OSError:
            pass

    # -- Bronze déjà connu
    def _load_known(self) -> None:
        revisit_days = int(self._env("AGENCY_REVISIT_DAYS", 7))
        empty_days = int(os.getenv("AGENCY_EMPTY_REVISIT_DAYS", "7"))
        self._known, self._final = set(), set()
        try:
            from immo.warehouse import connect
            with connect() as con:
                exists = con.execute(
                    "SELECT count(*) FROM information_schema.tables WHERE table_name=?",
                    [f"raw_{self.source}"]).fetchone()[0]
                if exists:
                    columns = {row[0] for row in con.execute(f"DESCRIBE raw_{self.source}").fetchall()}
                    if {"url", "_parser_version", "_collected_at", "details_json"} <= columns:
                        rows = con.execute(f"""
                            SELECT url,
                                   TRY_CAST(_collected_at AS TIMESTAMP) > now() - (? * INTERVAL '1 day'),
                                   json_extract_string(CAST(details_json AS VARCHAR), '$.Statut')
                            FROM raw_{self.source}
                            WHERE url IS NOT NULL
                              AND TRY_CAST(_parser_version AS INTEGER) = {self.PARSER_VERSION}
                        """, [revisit_days]).fetchall()
                        for url, recent, statut in rows:
                            if statut in STATUTS_FINAUX:
                                self._final.add(url)
                            elif recent:
                                self._known.add(url)
                self._known.update(row[0] for row in con.execute("""
                    SELECT url FROM connector_url_checks
                    WHERE source=? AND parser_version=? AND status='empty'
                      AND last_checked > now() - (? * INTERVAL '1 day')
                """, [self.source, self.PARSER_VERSION, empty_days]).fetchall())
        except Exception:
            pass

    def _candidate_urls(self) -> Iterator[str]:
        yield from self._sitemap_urls(self.sitemap_url)

    def _listing_page(self, url: str, extract) -> tuple[list[str], int | None] | None:
        """Télécharge une page de liste ; `extract(html)` → (URL de fiches, nb de pages)."""
        if not self._listing_open():
            return None
        result = self._get(url)
        return extract(result[0]) if result else None

    def _fresh_pages(self, url_for, extract, cap: int) -> Iterator[str]:
        """Résultats récents : pages 1.. jusqu'à une page entièrement déjà close."""
        for page in range(1, max(0, cap) + 1):
            listing = self._listing_page(url_for(page), extract)
            if not listing or not listing[0]:
                return
            yield from listing[0]
            if all(url in self._final for url in listing[0]):
                return

    def _backfill_pages(self, key: str, url_for, extract, per_run: int) -> Iterator[str]:
        """Rattrapage progressif de l'historique, curseur persistant par clé."""
        cursors = self._state.setdefault("history_next_page", {})
        page = int(cursors.get(key, 1))
        for _ in range(max(0, per_run)):
            listing = self._listing_page(url_for(page), extract)
            if not listing:
                return
            urls, total = listing
            yield from urls
            page += 1
            cursors[key] = page
            self._state["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._save_state(self._state)
            if not urls or (total and page > total):
                return

    def _record_empty(self, urls: list[str]) -> None:
        if not urls:
            return
        try:
            from immo.warehouse import connect
            with connect() as con:
                con.executemany("""
                    INSERT INTO connector_url_checks (source,url,parser_version,last_checked,status)
                    VALUES (?,?,?,now(),'empty')
                    ON CONFLICT (source,url,parser_version) DO UPDATE SET
                        last_checked=excluded.last_checked,status=excluded.status
                """, [(self.source, url, self.PARSER_VERSION) for url in urls])
        except Exception:
            pass

    # -- collecte
    def fetch(self, c: Criteria) -> Iterator[dict]:
        started = time.monotonic()
        limit = c.max_pages or int(self._env("AGENCY_MAX_URLS_PER_RUN", 0))
        self._failures = 0
        self._state = self._load_state()
        self._load_known()
        skip = self._known | self._final
        candidates: list[str] = []
        visited: set[str] = set()
        for url in self._candidate_urls():
            if url in visited or url in skip:
                continue
            visited.add(url)
            candidates.append(url)
            if limit and len(candidates) >= limit:
                break
        workers = min(3, max(1, int(os.getenv(f"AGENCY_FETCH_WORKERS_{self._suffix}", "2"))))
        breaker = max(1, int(self._env("AGENCY_CIRCUIT_BREAKER", 30)))
        deadline = started + max(60, int(self._env("AGENCY_MAX_RUNTIME_SECONDS", 2700)))
        requested = succeeded = parsed = parse_errors = consecutive = 0
        empty: list[str] = []
        circuit_open = False
        queue = iter(candidates)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"{self.source}-fetch") as pool:
            pending = {}

            def submit() -> bool:
                nonlocal requested
                try:
                    url = next(queue)
                except StopIteration:
                    return False
                pending[pool.submit(self._fetch_detail, url)] = url
                requested += 1
                return True

            for _ in range(workers * 2):
                if not submit():
                    break
            while pending and time.monotonic() < deadline:
                done, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                for future in done:
                    url = pending.pop(future)
                    result = future.result()
                    if result is None:
                        consecutive += 1
                    else:
                        consecutive = 0
                        succeeded += 1
                        count = 0
                        try:
                            for item in self.parse_page(*result):
                                item["_parser_version"] = self.PARSER_VERSION
                                count += 1
                                parsed += 1
                                yield item
                        except (ValueError, TypeError, KeyError, AttributeError, IndexError,
                                json.JSONDecodeError):
                            parse_errors += 1
                        if count == 0:
                            empty.append(url)
                    if consecutive >= breaker:
                        circuit_open = True
                    while not circuit_open and len(pending) < workers * 2 and time.monotonic() < deadline:
                        if not submit():
                            break
            for future in pending:
                future.cancel()
        self._record_empty(empty)
        print(
            f"Diagnostic {self.source}: {len(candidates)} candidates, {requested} requêtes, "
            f"{succeeded} réponses, {parsed} annonces, {parse_errors} erreurs de parsing, "
            f"{len(empty)} URL vides, {len(self._final)} ventes closes ignorées, "
            f"{time.monotonic() - started:.1f}s{' (coupe-circuit ouvert)' if circuit_open else ''}.",
            flush=True,
        )


# ---------------------------------------------------------------- Licitor

def _first(pattern: str, text: str, flags=re.S | re.I) -> str | None:
    match = re.search(pattern, text, flags)
    return match.group(1) if match else None


def _statut(h3: str, h4: str) -> tuple[str, float | None, float | None]:
    """(statut, mise à prix, prix adjugé) depuis les titres d'un lot Licitor."""
    head, sub = plain(h3), plain(h4)
    mise = parse_amount(_first(r"mise a prix\s*:?\s*([\d .,  ]+)", sub + " " + head, re.I))
    if head.startswith("adjudication"):
        return "adjugé", mise, parse_amount(head.split(":", 1)[-1])
    if "carence" in head:
        return "carence", mise, None
    if re.search(r"non requise|retir|annul|radi|suspendu", head):
        return "retiré", mise, None
    if re.search(r"report|renvoy", head):
        return "reporté", mise, None
    return "à venir", mise, None


class LicitorConnector(EnchereConnector):
    """Annonces légales Licitor : ventes à venir et historique des adjudications."""

    BASE = "https://www.licitor.com"
    REGIONS = (
        "paris-et-ile-de-france", "sud-est-mediterrannee", "regions-du-nord-est",
        "bretagne-grand-ouest", "centre-loire-limousin", "sud-ouest-pyrenees",
    )
    LOT_LINK = re.compile(r'href="(/annonce/\d+/\d+/\d+/vente-aux-encheres/[^"]+?/\d+\.html)"')

    def __init__(self) -> None:
        super().__init__("licitor", self.BASE + "/")

    def _extract(self, html: str) -> tuple[list[str], int | None]:
        urls = list(dict.fromkeys(urljoin(self.BASE, link) for link in self.LOT_LINK.findall(html)))
        total = _first(r'class="PageTotal"><span class="Slash">/</span>\s*(\d+)', html)
        return urls, int(total) if total else None

    def _url(self, region: str, kind: str):
        return lambda page: f"{self.BASE}/ventes-aux-encheres-immobilieres/{region}/{kind}.html?p={page}"

    def _upcoming(self) -> Iterator[str]:
        cap = int(self._env("LICITOR_UPCOMING_MAX_PAGES", 80))
        for region in self.REGIONS:
            page, total = 1, 1
            while page <= min(total, cap):
                listing = self._listing_page(self._url(region, "prochaines-ventes")(page), self._extract)
                if not listing or not listing[0]:
                    break
                yield from listing[0]
                total = listing[1] or page
                page += 1

    def _fresh_results(self) -> Iterator[str]:
        cap = int(self._env("LICITOR_FRESH_MAX_PAGES", 20))
        for region in self.REGIONS:
            yield from self._fresh_pages(self._url(region, "historique-des-adjudications"), self._extract, cap)

    def _backfill(self) -> Iterator[str]:
        per_run = int(self._env("LICITOR_HISTORY_PAGES_PER_RUN", 20))
        for region in self.REGIONS:
            yield from self._backfill_pages(
                region, self._url(region, "historique-des-adjudications"), self._extract, per_run)

    def _candidate_urls(self) -> Iterator[str]:
        yield from round_robin(self._upcoming(), self._fresh_results(), self._backfill())

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        number = _first(r'<p class="Number">\s*(\d+)\s*</p>', html)
        block = _first(r'<section class="AddressBlock">(.*?)</section>', html)
        if not number or not block:
            return
        court = clean_text(_first(r'<p class="Court">(.*?)</p>', html))
        sale_type = clean_text(_first(r'<p class="Type">(.*?)</p>', html))
        date_vente = iso_local(_first(r'<p class="Date">\s*<time datetime="([^"]+)"', html))
        published = _first(r'<p class="PublishingDate">.*?datetime="([^"]+)"', html)
        location = block.split('<div class="Location">', 1)[-1] if '<div class="Location">' in block else ""
        city = clean_text(_first(r'<p class="City">(.*?)</p>', location))
        street = clean_text(_first(r'<p class="Street">(.*?)</p>', location)).replace("\n", ", ")
        coords = re.search(r"maps\?q=(-?\d+\.\d+),(-?\d+\.\d+)", location)
        visits = clean_text(_first(r'<p class="Visits">(.*?)</p>', location))
        lawyer = clean_text(_first(r'<div class="Trust">\s*<h3>(.*?)</h3>', html))
        extra = clean_text(_first(r'<p class="AdditionalText">(.*?)</p>', html))
        documents = [{"titre": clean_text(label), "url": href} for href, label in re.findall(
            r'<a href="(https?://[^"]+\.pdf)"[^>]*>(.*?)</a>', _first(r'<div class="Related">(.*?)</div>', html) or "")]
        mode = "enchere_notariale" if "notari" in plain(sale_type) else self.MODE_VENTE
        lots = [chunk for chunk in block.split('<div class="Location">', 1)[0].split('<div class="Lot">')[1:]]
        for index, lot in enumerate(lots, start=1):
            parts = re.findall(r"<h2>(.*?)</h2>\s*(?:<p>(.*?)</p>)?", lot, re.S)
            titles = [clean_text(title) for title, _ in parts]
            texts = [clean_text(text) for _, text in parts]
            statut, mise, adjuge = _statut(clean_text(_first(r"<h3>(.*?)</h3>", lot)),
                                           clean_text(_first(r"<h4>(.*?)</h4>", lot)))
            if mise is None and adjuge is None and statut == "à venir":
                continue
            description = "\n".join(f"{title} {text}".strip() for title, text in zip(titles, texts))
            body = "\n\n".join(part for part in (description, extra) if part)
            kind = type_from_text(titles[0] if titles else description)
            surface = parse_surface(texts[0] if texts else description)
            identifier = number if len(lots) == 1 else f"{number}-{index}"
            details = {
                "Statut": statut, "Mise à prix": mise, "Tribunal": court, "Avocat": lawyer,
                "Occupation": occupation(description), "Visites": visits or None,
                "Adresse": street or None, "Type de vente": sale_type or None,
                "Lot": f"{index}/{len(lots)}" if len(lots) > 1 else None,
                "Composition": titles if len(titles) > 1 else None,
                "Documents": documents, "Clé enchère": cle_enchere(court, date_vente, city, mise),
            }
            yield make_record(
                id=identifier, name=f"{titles[0] if titles else 'Bien'} - {city}".strip(" -"),
                price=mise, prix_adjuge=adjuge, surface=None if kind == "terrain" else surface,
                land_surface=land_surface(kind, description, surface),
                rooms=parse_rooms(description), zipcode=postcode_from_city(city), city=city or None,
                lat=coords.group(1) if coords else None, lng=coords.group(2) if coords else None,
                type_hint=kind, url=page_url, body=body, seller_name=lawyer or None,
                published_at=iso_local(published), reference_annonce=number,
                mode_vente=mode, date_vente=date_vente, details=details,
                raw_payload=json.dumps({"lot": clean_text(lot), "court": court, "date": date_vente},
                                       ensure_ascii=False),
            )


register(LicitorConnector())
