"""Immonot (annonces des notaires) après la refonte Nuxt de juillet 2026.

Les fiches ``/immobilier-notaire/detail/<id>/<slug>.html`` sont listées par
``sitemap/annonce_detail_ancien/chunk-N.xml``. Le JSON-LD est un @graph dont
le nœud BuyAction (ou RentAction) porte prix, adresse et objet ; le reste est
lu dans le HTML rendu côté serveur.
"""
from collections.abc import Iterator
from html import unescape
import json
import re

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


SITEMAP_INDEX = "https://www.immonot.com/sitemap.xml"
# Ventes, viagers, enchères ; location conservée seulement pour les commerces.
SLUG_PREFIXES = ("achat-", "viager-", "encheres-", "location-fonds-et-ou-murs-commerciaux")


def _wanted(url: str) -> bool:
    if "/immobilier-notaire/detail/" not in url:
        return False
    return url.rstrip("/").rsplit("/", 1)[-1].startswith(SLUG_PREFIXES)


def _text(fragment: str | None) -> str:
    if not fragment:
        return ""
    fragment = re.sub(r"<svg\b.*?</svg>", " ", fragment, flags=re.S | re.I)
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    value = unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ").replace(" ", " ")
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines())
    return "\n".join(line for line in lines if line)


def _amount(value) -> int | float | None:
    """Premier nombre d'un libellé : "97 m²", "290 510 €", "5233 m²"."""
    if value is None:
        return None
    match = re.search(r"\d[\d   ]*(?:[,.]\d+)?", str(value))
    if not match:
        return None
    number = float(re.sub(r"[   ]", "", match.group(0)).replace(",", "."))
    return int(number) if number.is_integer() else number


def _types(node: dict) -> set[str]:
    value = node.get("@type")
    return set(value if isinstance(value, list) else [value])


def _type_hint(slug: str) -> str:
    for needle, hint in (
        ("fonds-et-ou-murs-commerciaux", "local commercial"), ("immeuble", "immeuble"),
        ("appartement", "appartement"), ("terrain", "terrain"),
        ("garage", "parking"), ("parking", "parking"), ("bien-agricole", "agricole"),
        ("propriete", "maison"), ("maison", "maison"),
    ):
        if needle in slug:
            return hint
    return "autre"


def _specs(html: str) -> dict[str, str]:
    """Paires libellé/valeur : bloc Caractéristiques et listes <dt>/<dd>."""
    specs: dict[str, str] = {}
    for label, value in re.findall(
        r'<p\b[^>]*>((?:(?!<p\b|</p>).)*)</p>\s*<p class="text-base font-bold[^"]*"[^>]*>(.*?)</p>',
        html, re.S,
    ):
        key, val = _text(label), _text(value)
        if key and val and len(key) < 80:
            specs[key] = val
    for label, value in re.findall(r"<dt\b[^>]*>(.*?)</dt>\s*<dd\b[^>]*>(.*?)</dd>", html, re.S):
        key, val = _text(label), _text(value)
        if key and val and len(key) < 120:
            specs[key] = val
    return specs


def _energy(html: str, kind: str) -> str | None:
    """Classe lue dans l'aria-label du SVG, sinon la barre la plus haute."""
    label = "Diagnostic de performance énergétique (DPE)" if kind == "dpe" else "effet de serre (GES)"
    text = unescape(html)
    match = re.search(re.escape(label) + r"\s*-\s*classe\s*([A-G])\b", text)
    if match:
        return match.group(1)
    start = text.find(label)
    if start < 0:
        return None
    svg = text[start:text.find("</svg>", start)]
    best = None
    for points, letter in re.findall(r'points="([^"]+)"[^>]*>\s*</polygon>\s*<text[^>]*>([A-G])</text>', svg):
        ys = [float(pair.split(",")[1]) for pair in points.split() if "," in pair]
        height = max(ys) - min(ys) if ys else 0
        if best is None or height > best[0]:
            best = (height, letter)
    return best[1] if best else None


class ImmonotConnector(AgencyJsonLdConnector):
    PARSER_VERSION = 20

    def __init__(self) -> None:
        super().__init__("immonot", SITEMAP_INDEX, _wanted)

    def _sitemap_urls(self, url: str, seen=None) -> Iterator[str]:
        if url != self.sitemap_url:
            yield from super()._sitemap_urls(url, seen)
            return
        # Lire seulement les fiches « ancien » (pas les listes, articles, neuf)
        # et alterner les fichiers pour répartir une collecte plafonnée.
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            chunks = re.findall(r"<loc>\s*([^<]*/annonce_detail_ancien/[^<]*?)\s*</loc>", response.text)
        except Exception:
            chunks = []
        streams = [iter(AgencyJsonLdConnector._sitemap_urls(self, chunk, set())) for chunk in chunks]
        while streams:
            active = []
            for stream in streams:
                try:
                    yield next(stream)
                    active.append(stream)
                except StopIteration:
                    pass
            streams = active

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        graph: list[dict] = []
        for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
            try:
                graph.extend(self._nodes(json.loads(block)))
            except (json.JSONDecodeError, TypeError):
                continue
        action = next((node for node in graph if _types(node) & {"BuyAction", "RentAction"}), None)
        if not action:
            return
        product = action.get("object") if isinstance(action.get("object"), dict) else {}
        address = (action.get("location") or {}).get("address") or product.get("address") or {}
        seller = action.get("seller") or action.get("landlord") or {}
        slug = page_url.rstrip("/").rsplit("/", 1)[-1].lower()
        from_url = re.search(r"/detail/([^/]+)/", page_url)
        identity = product.get("mpn") or (from_url.group(1) if from_url else None)
        if not identity:
            return
        rental = "RentAction" in _types(action) or slug.startswith("location-")
        specs = _specs(html)
        details: dict = {key: value for key, value in specs.items() if key != "Référence"}
        price_block = re.search(r'class="hero-detail-content-prix-detail"[^>]*>(.*?)</span>', html, re.S)
        price_text = _text(price_block.group(1)) if price_block else ""
        title_block = re.search(r'class="hero-detail-content-prix-title"[^>]*>(.*?)</span></span>', html, re.S)
        headline = _text(title_block.group(1)) if title_block else ""
        if price_text:
            details["Détail du prix"] = price_text
        net = re.search(r"([\d   ]+)€\s*\+\s*Honoraires de négociation TTC\s*:\s*([\d   ]+)€", price_text)
        if net:
            details["Prix hors honoraires"] = _amount(net.group(1))
            details["Honoraires de négociation"] = _amount(net.group(2))
        if slug.startswith("viager-"):
            details["Viager"] = "oui"
            if "bouquet" in headline.lower():
                details["Bouquet"] = _amount(headline)
            rent = re.search(r"Rente\s*:\s*([\d   ]+)€", price_text)
            if rent:
                details["Rente mensuelle viagère"] = _amount(rent.group(1))
        if rental:
            details["Loyer mensuel charges comprises"] = _amount(action.get("price") or headline)
        if slug.startswith("encheres-"):
            details["Mise à prix"] = _amount(action.get("price"))
        # Montants structurés : nombres annuels en euros sous les clés de finance.
        for label, key in (
            ("Taxe foncière", "Taxe foncière"), ("Travaux à prévoir", "Travaux à prévoir"),
            ("Montant des charges prévisionnelles annuelles moyen", "Charges annuelles de copropriété"),
            ("Charges annuelles", "Charges annuelles de copropriété"),
        ):
            if label in specs and _amount(specs[label]):
                details[key] = _amount(specs[label])
        diag = re.search(r"Diagnostic réalisé le\s*([^<]+)<", html)
        if diag and "1970" not in diag.group(1):
            details["Date du DPE"] = diag.group(1).strip()
        consumption = re.search(r">\s*(\d+)\s*</text>\s*<text[^>]*>\s*kWh/m²/an", html)
        emission = re.search(r">\s*(\d+)\s*</text>\s*<text[^>]*>\s*kg CO", html)
        if consumption:
            details["DPE consommation"] = int(consumption.group(1))
        if emission:
            details["GES émissions"] = int(emission.group(1))
        costs = re.search(r"dépenses annuelles d.énergie.{0,200}?entre\s*([\d   ]+)€\s*et\s*([\d   ]+)€",
                          unescape(re.sub(r"<[^>]+>", " ", html)), re.S)
        if costs:
            details["Coût énergétique annuel min"] = _amount(costs.group(1))
            details["Coût énergétique annuel max"] = _amount(costs.group(2))
        # Seul le lien Géorisques porte des coordonnées : centre de la commune.
        coords = re.search(r"georisques\.gouv\.fr[^\"']*?lon=(-?\d+(?:\.\d+)?)&(?:amp;)?lat=(-?\d+(?:\.\d+)?)", html)
        if coords:
            details["Précision cartographique"] = "approximative"
        images = [image for image in product.get("image") or [] if isinstance(image, str)]
        if not images:
            images = list(dict.fromkeys(re.findall(r"https://cdn\.notariat\.services/immonot/photo/[^\"'\s)]+", html)))
        description = product.get("description")
        body_block = re.search(r'class="mt-8 whitespace-pre-line[^"]*"[^>]*>(.*?)</div>', html, re.S)
        if body_block and len(_text(body_block.group(1))) >= len(description or ""):
            description = _text(body_block.group(1))
        dpe = _energy(html, "dpe")
        if not dpe and description:
            # Fiches « exemptées » dont la classe n'est donnée que dans le texte.
            found = re.search(r"\bDPE\s*(?:[:=-]\s*)?(?:classe\s*)?([A-G])\b", description)
            dpe = found.group(1) if found else None
        details = {key: value for key, value in details.items() if value not in (None, "", [], {})}
        yield {
            "id": identity, "name": product.get("name"), "price": _amount(action.get("price")),
            "surface": _amount(specs.get("Surface habitable")),
            "land_surface": _amount(specs.get("Surface terrain")),
            "rooms": _amount(specs.get("Nombre de pièces")), "bedrooms": _amount(specs.get("Chambres")),
            "zipcode": address.get("postalCode"), "city": address.get("addressLocality"),
            "lat": coords.group(2) if coords else None, "lng": coords.group(1) if coords else None,
            "type_hint": _type_hint(slug), "url": page_url, "body": description,
            "seller_name": seller.get("name"), "dpe": dpe, "ges": _energy(html, "ges"),
            "image_count": len(images), "published_at": None,
            "details_json": json.dumps(details, ensure_ascii=False),
            "images_json": json.dumps(images, ensure_ascii=False),
            "reference_annonce": specs.get("Référence"),
            "raw_payload": json.dumps(action, ensure_ascii=False, default=str),
        }


register(ImmonotConnector())
