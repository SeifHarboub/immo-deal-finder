from collections.abc import Iterator
import json
import os
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

from immo.connectors.base import Connector, register
from immo.schema import Criteria


class LeboncoinConnector(Connector):
    source = "leboncoin"

    @staticmethod
    def _url(c: Criteria) -> str:
        if c.search_url:
            return c.search_url
        params: dict[str, str] = {"category": "9"}
        if c.zones:
            params["locations"] = ",".join(c.zones)
        if c.prix_min is not None or c.prix_max is not None:
            low = "min" if c.prix_min is None else str(int(c.prix_min))
            high = "max" if c.prix_max is None else str(int(c.prix_max))
            params["price"] = f"{low}-{high}"
        return "https://www.leboncoin.fr/recherche?" + urlencode(params)

    @staticmethod
    def _map_ad(ad: dict) -> dict:
        attrs = {item.get("key"): item.get("value") for item in ad.get("attributes", [])}
        location = ad.get("location") or {}
        prices = ad.get("price") or [None]
        images = ad.get("images") or {}
        image_urls = (images.get("urls") or []) if isinstance(images, dict) else []
        owner = ad.get("owner") or {}
        return {
            "list_id": str(ad.get("list_id", "")), "subject": ad.get("subject"),
            "price": prices[0], "attr_square": attrs.get("square"),
            "attr_rooms": attrs.get("rooms"), "attr_land": attrs.get("land_plot_surface"),
            "attr_real_estate_type": attrs.get("real_estate_type"),
            "zipcode": location.get("zipcode"), "city": location.get("city"),
            "lat": location.get("lat"), "lng": location.get("lng"),
            "url": ad.get("url"),
            "first_publication_date": ad.get("first_publication_date"),
            "index_date": ad.get("index_date"),
            "expiration_date": ad.get("expiration_date"),
            "body": ad.get("body"),
            "category_id": ad.get("category_id") or (ad.get("category") or {}).get("id"),
            "status": ad.get("status"),
            "attr_bedrooms": attrs.get("bedrooms"),
            "attr_energy_rate": attrs.get("energy_rate"),
            "attr_ges": attrs.get("ges"),
            "attr_furnished": attrs.get("furnished"),
            "attr_elevator": attrs.get("elevator"),
            "attr_pool": attrs.get("pool"),
            "attr_parking": attrs.get("parking"),
            "seller_type": owner.get("type"),
            "seller_name": owner.get("name") or owner.get("store_name"),
            "image_count": len(image_urls),
            "attributes_json": json.dumps(ad.get("attributes") or [], ensure_ascii=False),
            "images_json": json.dumps(images, ensure_ascii=False),
            "owner_json": json.dumps(owner, ensure_ascii=False),
            # Copie native complète : aucun champ Leboncoin n'est perdu si le format évolue.
            "raw_payload": json.dumps(ad, ensure_ascii=False, default=str),
        }

    def fetch(self, c: Criteria) -> Iterator[dict]:
        payloads: list[dict] = []
        seen: set[str] = set()
        max_pages = c.max_pages or int(os.getenv("LEBONCOIN_MAX_PAGES", "100"))
        headless = os.getenv("LEBONCOIN_HEADLESS", "false").lower() in {"1", "true", "yes"}
        channel = os.getenv("LEBONCOIN_BROWSER_CHANNEL", "chrome").strip() or None
        profile = Path(os.getenv("LEBONCOIN_PROFILE", ".playwright-profile")).resolve()
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                str(profile), headless=headless, channel=channel,
                ignore_default_args=["--no-sandbox"],
            )
            page = context.pages[0] if context.pages else context.new_page()

            def capture(response) -> None:
                if "finder/search" in response.url and response.status == 200:
                    try:
                        payloads.append(response.json())
                    except Exception:
                        pass

            page.on("response", capture)
            page.goto(self._url(c), wait_until="domcontentloaded", timeout=90_000)
            # Laisse le temps de résoudre manuellement un éventuel captcha au premier lancement.
            captcha_wait = int(os.getenv("LEBONCOIN_CAPTCHA_WAIT", "600"))
            waited = 0
            while not payloads and waited < captcha_wait:
                page.wait_for_timeout(1000)
                waited += 1
                if waited == 3:
                    body = page.locator("body").inner_text(timeout=5000).lower()
                    if "accès temporairement restreint" in body:
                        screenshot = Path("data/leboncoin-blocked.png").resolve()
                        screenshot.parent.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(screenshot), full_page=False)
                        raise RuntimeError(
                            "LEBONCOIN_ACCESS_RESTRICTED: arrêt immédiat du lot pour protéger l'IP"
                        )
                    if "pas à un robot" in body or "sécuriser votre accès" in body:
                        print(
                            "CAPTCHA Leboncoin détecté. Le navigateur reste ouvert pour une "
                            "validation initiale ; le profil et les cookies seront conservés.",
                            flush=True,
                        )
            if not payloads:
                screenshot = Path("data/leboncoin-blocked.png").resolve()
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(screenshot), full_page=False)
                raise RuntimeError(
                    "Aucune réponse finder/search reçue. La page est probablement bloquée "
                    f"ou le format Leboncoin a changé. Capture : {screenshot}"
                )

            consumed = 0
            for page_number in range(max_pages):
                page.mouse.wheel(0, 5000)
                page.wait_for_timeout(1500)
                for payload in payloads[consumed:]:
                    for ad in payload.get("ads", []):
                        list_id = str(ad.get("list_id", ""))
                        if list_id and list_id not in seen:
                            seen.add(list_id)
                            yield self._map_ad(ad)
                consumed = len(payloads)
                self._pause()
                if page_number + 1 >= max_pages:
                    break
                next_page = page.locator("a[rel='next']")
                if next_page.count() == 0:
                    next_page = page.get_by_role("link", name="Page suivante")
                if next_page.count() == 0:
                    next_page = page.get_by_role("button", name="Page suivante")
                if next_page.count() == 0 or not next_page.first.is_enabled():
                    break
                next_page.first.click()
                page.wait_for_load_state("domcontentloaded")
            context.close()


register(LeboncoinConnector())
