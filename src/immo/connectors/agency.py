from collections.abc import Iterator
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import gzip
from html.parser import HTMLParser
import json
import os
import random
import threading
import time
from urllib.parse import urljoin
from xml.etree import ElementTree

import requests

from immo.connectors.base import Connector
from immo.schema import Criteria


REAL_ESTATE_TYPES = {"Product", "Offer", "Residence", "Apartment", "House", "SingleFamilyResidence"}
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


class _JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_json_ld = False
        self.blocks: list[str] = []
        self._current: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.lower() == "script" and values.get("type", "").lower() == "application/ld+json":
            self.in_json_ld = True
            self._current = []

    def handle_data(self, data: str) -> None:
        if self.in_json_ld:
            self._current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self.in_json_ld:
            self.blocks.append("".join(self._current))
            self.in_json_ld = False


class AgencyJsonLdConnector(Connector):
    """Template d'agence. Adapter principalement `_map_node` au JSON-LD du site."""

    PARSER_VERSION = 4

    def __init__(self, source: str, sitemap_url: str, url_filter=None) -> None:
        self.source = source
        self.sitemap_url = sitemap_url
        self.url_filter = url_filter or (lambda _url: True)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._thread_local = threading.local()

    def _detail_session(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers["User-Agent"] = USER_AGENT
            self._thread_local.session = session
        return session

    def _fetch_detail(self, url: str) -> tuple[str, str] | None:
        """Télécharge une fiche sans faire échouer le connecteur entier.

        Les erreurs transitoires sont retentées; une fiche supprimée ou une erreur
        persistante est simplement ignorée et pourra être revue au prochain cycle.
        """
        suffix = self.source.upper().replace("-", "_")
        retries = max(0, int(os.getenv(
            f"AGENCY_RETRIES_{suffix}", os.getenv("AGENCY_RETRIES", "2")
        )))
        timeout = float(os.getenv(
            f"AGENCY_TIMEOUT_{suffix}", os.getenv("AGENCY_TIMEOUT", "25")
        ))
        minimum = float(os.getenv(
            f"SCRAPE_MIN_DELAY_{suffix}", os.getenv("SCRAPE_MIN_DELAY", "1")
        ))
        maximum = float(os.getenv(
            f"SCRAPE_MAX_DELAY_{suffix}", os.getenv("SCRAPE_MAX_DELAY", "2")
        ))
        if maximum > 0:
            time.sleep(random.uniform(max(0, minimum), max(minimum, maximum)))

        for attempt in range(retries + 1):
            try:
                response = self._detail_session().get(url, timeout=timeout)
                if response.status_code == 404:
                    return None
                if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
                    if attempt < retries:
                        retry_after = response.headers.get("Retry-After", "")
                        delay = float(retry_after) if retry_after.isdigit() else 1.5 * (attempt + 1)
                        time.sleep(min(delay, 10))
                        continue
                    return None
                response.raise_for_status()
                return response.text, response.url
            except requests.RequestException:
                if attempt >= retries:
                    return None
                time.sleep(min(1.5 * (attempt + 1), 10))
        return None

    def _sitemap_urls(self, url: str, seen: set[str] | None = None) -> Iterator[str]:
        seen = seen or set()
        if url in seen:
            return
        seen.add(url)
        response = None
        for attempt in range(3):
            try:
                response = self.session.get(url, timeout=30)
                if response.status_code == 404:
                    return
                if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
                    if attempt < 2:
                        time.sleep(2 * (attempt + 1))
                        continue
                    self._sitemap_failed = True
                    return
                response.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 2:
                    self._sitemap_failed = True
                    return
                time.sleep(2 * (attempt + 1))
        if response is None:
            return
        content = gzip.decompress(response.content) if url.endswith(".gz") else response.content
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError:
            self._sitemap_failed = True
            return
        locations = [node.text.strip() for node in root.iter() if node.tag.endswith("loc") and node.text]
        if root.tag.endswith("sitemapindex"):
            for child in locations:
                yield from self._sitemap_urls(urljoin(url, child), seen)
        else:
            yield from (item for item in locations if self.url_filter(item))

    @staticmethod
    def _nodes(value) -> Iterator[dict]:
        if isinstance(value, list):
            for item in value:
                yield from AgencyJsonLdConnector._nodes(item)
        elif isinstance(value, dict):
            graph = value.get("@graph")
            if graph is not None:
                yield from AgencyJsonLdConnector._nodes(graph)
            yield value

    def _map_node(self, node: dict, page_url: str) -> dict | None:
        # Point d'adaptation : certains sites imbriquent prix, adresse ou surface autrement.
        node_type = node.get("@type")
        types = set(node_type if isinstance(node_type, list) else [node_type])
        if not types.intersection(REAL_ESTATE_TYPES):
            return None
        offers = node.get("offers") or (node if "price" in node else {})
        address = node.get("address") or {}
        geo = node.get("geo") or {}
        floor_size = node.get("floorSize") or {}
        return {
            "id": str(node.get("@id") or node.get("sku") or page_url),
            "name": node.get("name"), "price": offers.get("price"),
            "surface": floor_size.get("value") if isinstance(floor_size, dict) else floor_size,
            "zipcode": address.get("postalCode"), "city": address.get("addressLocality"),
            "lat": geo.get("latitude"), "lng": geo.get("longitude"),
            "type_hint": next(iter(types - {None}), None), "url": page_url,
        }

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        """Extrait une page; les connecteurs peuvent remplacer le JSON-LD."""
        parser = _JsonLdParser()
        parser.feed(html)
        for block in parser.blocks:
            try:
                value = json.loads(block)
            except (json.JSONDecodeError, TypeError):
                continue
            for node in self._nodes(value):
                mapped = self._map_node(node, page_url)
                if mapped:
                    yield mapped

    def fetch(self, c: Criteria) -> Iterator[dict]:
        suffix = self.source.upper().replace("-", "_")
        limit = c.max_pages or int(os.getenv(
            f"AGENCY_MAX_URLS_PER_RUN_{suffix}",
            os.getenv("AGENCY_MAX_URLS_PER_RUN", "0"),
        ))
        revisit_days = int(os.getenv("AGENCY_REVISIT_DAYS", "7"))
        empty_revisit_days = int(os.getenv("AGENCY_EMPTY_REVISIT_DAYS", "7"))
        known: set[str] = set()
        # Un connecteur reste indépendant du schéma Silver, mais peut consulter son
        # propre Bronze pour reprendre le sitemap au premier bien non récent.
        try:
            from immo.warehouse import connect
            with connect() as con:
                table_exists = con.execute(
                    "SELECT count(*) FROM information_schema.tables WHERE table_name=?",
                    [f"raw_{self.source}"],
                ).fetchone()[0]
                if table_exists:
                    columns = {row[0] for row in con.execute(f"DESCRIBE raw_{self.source}").fetchall()}
                    version_clause = (
                        f"AND TRY_CAST(_parser_version AS INTEGER) = {self.PARSER_VERSION}"
                        if "_parser_version" in columns else "AND false"
                    )
                    # Bronze est compacté à une ligne par annonce après chaque
                    # normalisation. Éviter GROUP BY/HAVING ici supprime un tri
                    # complet de la table avant chaque collecte nationale.
                    known = {row[0] for row in con.execute(f"""
                        SELECT url FROM raw_{self.source}
                        WHERE url IS NOT NULL {version_clause}
                          AND TRY_CAST(_collected_at AS TIMESTAMP)
                              > now() - (? * INTERVAL '1 day')
                    """, [revisit_days]).fetchall()}
                    known.update(row[0] for row in con.execute("""
                        SELECT url FROM connector_url_checks
                        WHERE source=? AND parser_version=? AND status='empty'
                          AND last_checked > now() - (? * INTERVAL '1 day')
                    """, [self.source, self.PARSER_VERSION, empty_revisit_days]).fetchall())
        except Exception:
            known = set()
        visited: set[str] = set()
        candidates: list[str] = []
        self._sitemap_failed = False
        # Le sitemap est toujours lu en entier, même quand la limite du cycle
        # est atteinte : sa liste complète sert d'inventaire des annonces encore
        # en ligne, et une annonce qui en sort est marquée retirée.
        for url in self._sitemap_urls(self.sitemap_url):
            if url in visited:
                continue
            visited.add(url)
            if url in known or (limit and len(candidates) >= limit):
                continue
            candidates.append(url)
        if not c.max_pages:
            try:
                from immo.lifecycle import record_inventory
                from immo.warehouse import connect
                with connect() as con:
                    record_inventory(con, self.source, visited, complete=not self._sitemap_failed)
            except Exception as exc:
                print(f"Inventaire {self.source} non enregistré : {exc}", flush=True)

        workers = max(1, int(os.getenv(
            f"AGENCY_FETCH_WORKERS_{suffix}", os.getenv("AGENCY_FETCH_WORKERS", "3")
        )))
        # La limite reste explicite et configurable. Douze workers est un bon
        # compromis pour les catalogues publics testés : le réseau demeure le
        # goulet, sans créer des centaines de connexions simultanées.
        worker_cap = max(1, int(os.getenv("AGENCY_FETCH_WORKERS_CAP", "16")))
        workers = min(workers, worker_cap, max(1, len(candidates)))
        prefetch_factor = max(1, min(5, int(os.getenv("AGENCY_PREFETCH_FACTOR", "3"))))
        inflight_target = min(len(candidates), workers * prefetch_factor)
        breaker_threshold = max(1, int(os.getenv(
            f"AGENCY_CIRCUIT_BREAKER_{suffix}",
            os.getenv("AGENCY_CIRCUIT_BREAKER", "30"),
        )))
        max_runtime = max(60, int(os.getenv(
            f"AGENCY_MAX_RUNTIME_SECONDS_{suffix}",
            os.getenv("AGENCY_MAX_RUNTIME_SECONDS", "2700"),
        )))
        deadline = time.monotonic() + max_runtime
        candidate_iter = iter(candidates)
        requested = completed = succeeded = parsed = parse_errors = 0
        empty_urls: list[str] = []
        circuit_open = False
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"{self.source}-fetch") as pool:
            pending = {}
            for _ in range(inflight_target):
                try:
                    url = next(candidate_iter)
                except StopIteration:
                    break
                pending[pool.submit(self._fetch_detail, url)] = url
                requested += 1

            # File bornée à `workers`: les réponses terminées sont libérées
            # immédiatement et un site ralenti ne peut plus bloquer un cycle
            # pendant des heures ou accumuler 15 000 pages en mémoire.
            while pending and time.monotonic() < deadline:
                done, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                for future in done:
                    checked_url = pending.pop(future, None)
                    completed += 1
                    result = future.result()
                    if result is not None:
                        succeeded += 1
                        html, final_url = result
                        page_items = 0
                        try:
                            for item in self.parse_page(html, final_url):
                                item["_parser_version"] = self.PARSER_VERSION
                                parsed += 1
                                page_items += 1
                                yield item
                        except (ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError):
                            # Une fiche malformée est ignorée sans annuler les
                            # milliers d'autres fiches valides du connecteur.
                            parse_errors += 1
                        if page_items == 0 and checked_url:
                            empty_urls.append(checked_url)
                    if completed >= breaker_threshold and succeeded == 0:
                        circuit_open = True
                    while (
                        not circuit_open
                        and len(pending) < inflight_target
                        and time.monotonic() < deadline
                    ):
                        try:
                            url = next(candidate_iter)
                        except StopIteration:
                            break
                        pending[pool.submit(self._fetch_detail, url)] = url
                        requested += 1
            for future in pending:
                future.cancel()
        if empty_urls:
            try:
                from immo.warehouse import connect
                with connect() as con:
                    con.executemany("""
                        INSERT INTO connector_url_checks
                            (source,url,parser_version,last_checked,status)
                        VALUES (?,?,?,now(),'empty')
                        ON CONFLICT (source,url,parser_version) DO UPDATE SET
                            last_checked=excluded.last_checked,status=excluded.status
                    """, [
                        (self.source, url, self.PARSER_VERSION)
                        for url in empty_urls
                    ])
            except Exception:
                # Le cache négatif accélère les cycles suivants mais ne doit
                # jamais faire échouer une collecte utile.
                pass
        elapsed = max_runtime - max(0, deadline - time.monotonic())
        print(
            f"Diagnostic {self.source}: {len(candidates)} candidates, "
            f"{requested} requêtes, {succeeded} réponses, {parsed} annonces, "
            f"{requested - succeeded} réponses inutilisables, "
            f"{parse_errors} erreurs de parsing, {len(empty_urls)} URL vides, {elapsed:.1f}s"
            f"{' (coupe-circuit ouvert)' if circuit_open else ''}.",
            flush=True,
        )
