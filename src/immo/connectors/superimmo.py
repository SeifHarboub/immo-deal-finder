from collections.abc import Iterator
from html import unescape
import json
import os
import re
import time
from urllib.parse import urljoin

import requests

from immo.connectors.agency import USER_AGENT
from immo.connectors.base import Connector, register
from immo.schema import Criteria


class SuperimmoConnector(Connector):
    source = "superimmo"
    root = "https://www.superimmo.com"

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def _get(self, url: str) -> requests.Response:
        response = None
        for attempt in range(4):
            response = self.session.get(url, timeout=30)
            if response.status_code not in {429, 502, 503, 504}:
                response.raise_for_status()
                return response
            delay = min(60, int(response.headers.get("Retry-After", 0) or 0) or 5 * (attempt + 1))
            time.sleep(delay)
        assert response is not None
        response.raise_for_status()
        return response

    @staticmethod
    def _record(html: str, url: str) -> dict | None:
        title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        if not title_match:
            return None
        title = unescape(title_match.group(1))
        kind = re.search(r"Vente\s+(maison|appartement|terrain|immeuble)", title, re.I)
        surface = re.search(r"([\d,.]+)\s*m²", title, re.I)
        rooms = re.search(r"(\d+)\s*pièces?", title, re.I)
        postal = re.search(r"\((\d{5})(?:,\s*\d{5})?\)", title)
        city = re.search(r"m²\s+(.+?)\s+\(\d{5}", title)
        price = re.search(r"Prix de vente(?:&nbsp;|\s)*:(?:&nbsp;|\s)*([\d&nbsp; ]+)(?:&nbsp;|\s)*€", html)
        bedrooms = re.search(r"(\d+)\s+chambres?", unescape(html), re.I)
        land = re.search(r"ter\.\s*([\d&nbsp; ]+)\s*m²", html, re.I)
        description = re.search(r'<meta (?:name|property)="(?:description|og:description)" content="([^"]*)"', html)
        external_id = url.rstrip("/").rsplit("-", 1)[-1]
        return {
            "id": external_id, "name": title,
            "price": price.group(1).replace("&nbsp;", "").replace(" ", "") if price else None,
            "surface": surface.group(1).replace(",", ".") if surface else None,
            "land_surface": land.group(1).replace("&nbsp;", "").replace(" ", "") if land else None,
            "rooms": rooms.group(1) if rooms else None,
            "bedrooms": bedrooms.group(1) if bedrooms else None,
            "zipcode": postal.group(1) if postal else None,
            "city": city.group(1) if city else None,
            "lat": None, "lng": None, "type_hint": kind.group(1) if kind else None,
            "url": url, "body": unescape(description.group(1)) if description else None,
            "raw_payload": json.dumps({"title": title}, ensure_ascii=False),
        }

    def fetch(self, c: Criteria) -> Iterator[dict]:
        limit = c.max_pages or int(os.getenv(
            "AGENCY_MAX_URLS_PER_RUN_SUPERIMMO",
            os.getenv("AGENCY_MAX_URLS_PER_RUN", "100"),
        ))
        known: set[str] = set()
        try:
            from immo.warehouse import connect
            with connect() as con:
                if con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='raw_superimmo'").fetchone()[0]:
                    known = {row[0] for row in con.execute("SELECT DISTINCT url FROM raw_superimmo WHERE url IS NOT NULL").fetchall()}
        except Exception:
            pass
        processed = 0
        page = 1
        seen: set[str] = set()
        while not limit or processed < limit:
            response = self._get(f"{self.root}/achat?page={page}")
            links = [urljoin(self.root, unescape(path)) for path in re.findall(
                r'href="(/annonces/achat-[^"]+)"', response.text
            )]
            links = list(dict.fromkeys(links))
            if not links:
                break
            fresh_on_page = 0
            for url in links:
                if url in seen or url in known:
                    continue
                seen.add(url)
                detail = self._get(url)
                record = self._record(detail.text, detail.url)
                if record:
                    yield record
                    processed += 1
                    fresh_on_page += 1
                if limit and processed >= limit:
                    return
                self._pause()
            page += 1
            if fresh_on_page == 0 and page > 20:
                break


register(SuperimmoConnector())
