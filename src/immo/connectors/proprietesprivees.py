from collections.abc import Iterator
from html import unescape
import json
import re

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


TYPES = {
    "house": "maison", "apartment": "appartement", "building": "immeuble",
    "land": "terrain", "business": "fonds_commerce", "commercial": "local_commercial",
    "premises": "local_commercial", "office": "bureau",
}
FLAGS = {
    "attic": "Grenier", "cellar": "Cave", "swimingpool": "Piscine", "balcony": "Balcon",
    "terrace": "Terrasse", "elevator": "Ascenseur", "fireplace": "Cheminée", "garage": "Garage",
    "veranda": "Véranda", "privateGarden": "Jardin privatif", "basement": "Sous-sol",
    "loggia": "Loggia", "greenSpace": "Espace vert", "intercom": "Interphone",
    "equippedKitchen": "Cuisine équipée", "disabledAccess": "Accès handicapé",
    "furniture": "Meublé", "exclusive": "Exclusivité", "prestige": "Prestige",
    "commitment": "Sous compromis", "buildingPermit": "Permis de construire",
    "urbanPlanningCertification": "Certificat d'urbanisme", "batchSales": "Vente en lots",
}


class _JsReader:
    """Lecteur minimal de littéraux JavaScript (sortie `window.__NUXT__` de Nuxt 2)."""

    def __init__(self, source: str, names: dict | None = None) -> None:
        self.source, self.index, self.names = source, 0, names or {}

    def _space(self) -> None:
        while self.index < len(self.source) and self.source[self.index] in " \t\r\n":
            self.index += 1

    def value(self):
        self._space()
        char = self.source[self.index]
        if char in "\"'":
            return self._string(char)
        if char == "{":
            return self._object()
        if char == "[":
            return self._array()
        match = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?").match(self.source, self.index)
        if match:
            self.index = match.end()
            text = match.group(0)
            return float(text) if any(c in text for c in ".eE") else int(text)
        match = re.compile(r"void 0|Array\(\d+\)|[A-Za-z_$][\w$]*").match(self.source, self.index)
        if not match:
            raise ValueError(f"Littéral JS inattendu à {self.index}")
        self.index = match.end()
        word = match.group(0)
        if word.startswith("Array("):
            return []
        return {"true": True, "false": False, "null": None, "void 0": None}.get(
            word, self.names.get(word)
        )

    def _string(self, quote: str) -> str:
        end = self.index + 1
        while self.source[end] != quote:
            end += 2 if self.source[end] == "\\" else 1
        raw = self.source[self.index + 1:end]
        self.index = end + 1
        return json.loads('"' + raw.replace('\\"' if quote == "'" else "\0", '"').replace("\\'", "'")
                          .replace('"', '\\"').replace('\\\\"', '\\"') + '"') if raw else ""

    def _object(self) -> dict:
        self.index += 1
        result = {}
        while True:
            self._space()
            if self.source[self.index] == "}":
                self.index += 1
                return result
            if self.source[self.index] in "\"'":
                key = self._string(self.source[self.index])
            else:
                match = re.compile(r"[\w$]+").match(self.source, self.index)
                key, self.index = match.group(0), match.end()
            self._space()
            self.index += 1  # ':'
            result[key] = self.value()
            self._space()
            if self.source[self.index] == ",":
                self.index += 1

    def _array(self) -> list:
        self.index += 1
        result = []
        while True:
            self._space()
            if self.source[self.index] == "]":
                self.index += 1
                return result
            result.append(self.value())
            self._space()
            if self.source[self.index] == ",":
                self.index += 1


def _split_top_level(source: str, separator: str) -> list[str]:
    """Découpe sur `separator` hors chaînes, objets et tableaux."""
    parts, depth, quote, start, index = [], 0, None, 0, 0
    while index < len(source):
        char = source[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        elif char == separator and depth == 0:
            parts.append(source[start:index])
            start = index + 1
        index += 1
    parts.append(source[start:])
    return parts


def nuxt_trade(html: str) -> dict | None:
    """Reconstitue l'objet annonce de `window.__NUXT__=(function(a,b,…){…}(…))`."""
    match = re.search(r"__NUXT__=\(function\(([^)]*)\)\{(.*)\}\((.*)\)\);?\s*</script>", html, re.S)
    if not match:
        return None
    params, body, args = match.group(1).split(","), match.group(2), match.group(3)
    names: dict = {}
    for name, raw in zip(params, _split_top_level(args, ",")):
        try:
            names[name] = _JsReader(raw.strip()).value()
        except (ValueError, IndexError, json.JSONDecodeError):
            names[name] = None
    variable = re.search(r'"/trades/[^"]+":\{[^{}]*data:([\w$]+)\}', body)
    if not variable:
        return None
    target = variable.group(1)
    trade: dict = {}
    prefix = f"{target}."
    for statement in _split_top_level(body, ";"):
        statement = statement.strip()
        if not statement.startswith(prefix) or "=" not in statement:
            continue
        key, raw = statement[len(prefix):].split("=", 1)
        if not re.fullmatch(r"[\w$]+", key):
            continue
        try:
            trade[key] = _JsReader(raw, names).value()
        except (ValueError, IndexError, json.JSONDecodeError):
            continue
    return trade or None


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"<br\s*/?>|</br>|</p>", "\n", text, flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", " ", text)).replace("\xa0", " ")
    lines = (re.sub(r"\s+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line and not re.fullmatch(r"[.\s]+", line)) or None


class ProprietesPriveesConnector(AgencyJsonLdConnector):
    """Propriétés-Privées : sitemap global, état Nuxt de la fiche `/annonces/<réf>`."""

    def __init__(self) -> None:
        super().__init__(
            "proprietesprivees", "https://www.proprietes-privees.com/sitemap.xml",
            lambda url: bool(re.search(r"proprietes-privees\.com/annonces/[^/?#]+$", url)),
        )

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        trade = nuxt_trade(html)
        if not trade or trade.get("prestation") != "buy" or trade.get("rental"):
            return
        if trade.get("sold") or trade.get("removed"):
            return
        kind = str(trade.get("type") or "").lower()
        if kind in {"parking", "garage"}:
            return
        type_hint = TYPES.get(kind, "autre")
        location = trade.get("location") or {}
        # Coordonnées du centre de la commune (location = ville), pas du bien.
        approximate = location.get("type") == "city"
        details = {label: "oui" for key, label in FLAGS.items() if trade.get(key) is True}
        mandatary = trade.get("mandatary") or {}
        details.update({
            "Référence": trade.get("reference"), "Catégorie": trade.get("category"),
            "Salles de bain": trade.get("bathroomCount") or None,
            "Salles d'eau": trade.get("showerRoomCount") or None,
            "Niveaux": trade.get("levelsCount") or None,
            "Prix au m² annoncé": trade.get("pricePerMeter"),
            "Prix hors honoraires": _float(trade.get("priceWithoutFees")),
            "Honoraires à la charge": "acquéreur" if trade.get("feesPayableByBuyer") else "vendeur",
            "DPE consommation": trade.get("dpeIntakeValue"), "GES émissions": trade.get("dpeGesValue"),
            "Mandat": trade.get("mandateName"),
            "Vente aux enchères": "oui" if trade.get("auction") else None,
            "Taxe foncière": _float(trade.get("propertyTax") or trade.get("landTax")),
            "Charges annuelles de copropriété": _float(
                trade.get("annualCoOwnershipCharges") or trade.get("coOwnershipCharges")
            ),
            "Nombre de lots": trade.get("coOwnershipLotsCount") or trade.get("lotsCount"),
            "Département": ((location.get("parent") or {}).get("label")),
            "Conseiller": " ".join(filter(None, [mandatary.get("firstname"), mandatary.get("lastname")])),
            "Secteur du conseiller": mandatary.get("zone"),
        })
        description = _clean(trade.get("description"))
        if trade.get("category") == "life" or re.search(r"\bviager\b", f"{trade.get('title')} {description}", re.I):
            details["Viager"] = "oui"
        if approximate:
            details["Précision cartographique"] = "approximative"
        details = {key: value for key, value in details.items() if value not in (None, "", [], {})}
        images = [url for url in trade.get("picturesUrls") or [] if isinstance(url, str)]
        reference = trade.get("reference") or page_url.rsplit("/", 1)[-1]
        dpe = str(trade.get("dpeIntakeTag") or "").upper()
        ges = str(trade.get("dpeGesTag") or "").upper()
        yield {
            "id": reference, "name": trade.get("title"), "price": _float(trade.get("salePrice")),
            "surface": trade.get("surfaceInSquareMetres") or trade.get("surface"),
            "land_surface": trade.get("landSurface") or None,
            "rooms": trade.get("roomCount"), "bedrooms": trade.get("bedroomCount"),
            "zipcode": location.get("code"), "city": str(location.get("label") or "").title() or None,
            "lat": location.get("latitude"), "lng": location.get("longitude"), "type_hint": type_hint,
            "url": page_url, "body": description,
            "dpe": dpe if dpe in tuple("ABCDEFG") else None,
            "ges": ges if ges in tuple("ABCDEFG") else None,
            "seller_name": details.get("Conseiller"), "published_at": trade.get("publishedAt") or trade.get("createdAt"),
            "details_json": json.dumps(details, ensure_ascii=False),
            "images_json": json.dumps(images, ensure_ascii=False),
            "image_count": len(images), "reference_annonce": reference,
            "raw_payload": json.dumps(
                {key: value for key, value in trade.items() if key not in {"closeTrades", "mandatary", "picturesUrls"}},
                ensure_ascii=False, default=str,
            ),
        }


def _float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


register(ProprietesPriveesConnector())
