from collections.abc import Iterator
from html import unescape
import json
import re

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


TYPES = {
    "appartement": "appartement", "maison": "maison", "immeuble": "immeuble",
    "terrain": "terrain", "local-commercial": "local_commercial",
    "local commercial": "local_commercial", "bureau": "bureau", "bureaux": "bureau",
}
EQUIPMENTS = {
    "cave": "Cave", "ascenseur": "Ascenseur", "garage": "Garage", "balcon": "Balcon",
    "box": "Box", "jardin": "Jardin", "piscine": "Piscine", "cheminee": "Cheminée",
    "climatisation": "Climatisation", "dependances": "Dépendances", "atelier": "Atelier",
    "digicode": "Digicode", "interphone": "Interphone", "logeGardien": "Loge gardien",
    "parkingCollectif": "Parking collectif", "doubleVitrage": "Double vitrage",
    "cuisineEquipee": "Cuisine équipée", "terrasse": "Terrasse", "espaceVert": "Espace vert",
}


def _transfer_state(html: str) -> dict:
    """Décode l'état Angular `serverApp-state` (entités &q; &a; …)."""
    match = re.search(r'<script id="serverApp-state" type="application/json">(.*?)</script>', html, re.S)
    if not match:
        return {}
    raw = match.group(1)
    for entity, char in (("&q;", '"'), ("&s;", "'"), ("&l;", "<"), ("&g;", ">"), ("&a;", "&")):
        raw = raw.replace(entity, char)
    return json.loads(raw)


class FonciaConnector(AgencyJsonLdConnector):
    """Foncia : sitemap des ventes puis réponse API embarquée dans la fiche HTML."""

    def __init__(self) -> None:
        super().__init__(
            "foncia", "https://fr.foncia.com/achat-annonce.xml",
            lambda url: "/achat/" in url and url.endswith(".htm") and "/parking/" not in url,
        )

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        state = _transfer_state(html)
        data = next((
            value.get("body") for key, value in state.items()
            if re.search(r"/annonces/annonces/transaction/[^/]+\.$", key) and isinstance(value, dict)
        ), None)
        if not isinstance(data, dict) or data.get("typeAnnonce") != "transaction":
            yield from self._from_json_ld(html, page_url)
            return
        type_hint = TYPES.get(str(data.get("typeBien") or "").lower())
        if not type_hint:
            return
        location = data.get("localisation") or {}
        locality = location.get("locality") or {}
        point = location.get("geoPoint") or {}
        surface = data.get("surface") or {}
        features = data.get("caracteristiques") or {}
        copro = data.get("copro") or {}
        dpe = data.get("dpe") or {}
        monthly = copro.get("chargesMensuelles")
        # Pour un immeuble, Foncia recopie parfois la surface bâtie dans `terrain`.
        land = surface.get("terrain")
        if type_hint in {"immeuble", "appartement"} and land == surface.get("habitable"):
            land = None
        details = {
            label: "oui" for key, label in EQUIPMENTS.items()
            if isinstance(features.get(key), dict) and features[key].get("available")
        }
        details.update({
            "Référence": data.get("reference"), "Code INSEE": locality.get("codeInsee"),
            "Département": (locality.get("departement") or {}).get("libelleDisplay"),
            "Année de construction": features.get("anneeConstruction"),
            "Étage": (features.get("etage") or {}).get("number"),
            "Nombre d'étages": data.get("nbEtage"),
            "Salles de bain": data.get("nbSDB"), "Salles d'eau": data.get("nbSDE"),
            "Surface Carrez": surface.get("carrez"), "Surface totale": surface.get("totale"),
            "Mode de chauffage": features.get("modeChauffage"),
            "Type de chauffage": features.get("typeChauffage"),
            "Cuisine": features.get("etatCuisine"),
            "Mitoyenneté": "oui" if features.get("mitoyennete") else None,
            "Accès handicapé": "oui" if features.get("accesHandicape") else None,
            "Copropriété": "oui" if copro.get("copro") else None,
            "Nombre de lots": copro.get("nbLot"),
            "Charges annuelles de copropriété": round(float(monthly) * 12, 2) if monthly else None,
            "Procédure de copropriété en cours": "oui" if copro.get("enDifficulte") else None,
            "DPE consommation": dpe.get("KW"), "GES émissions": dpe.get("GES"),
            "Date du DPE": dpe.get("date"),
            "Coût énergétique annuel min": dpe.get("energyPriceMin"),
            "Coût énergétique annuel max": dpe.get("energyPriceMax"),
            "Exclusivité": "oui" if data.get("exclusivite") else None,
            "Type de mandat": data.get("typeMandat"),
            "Visite virtuelle": data.get("urlVisite360"),
            "Mise à jour": data.get("lastUpdateDate"),
        })
        description = (data.get("description") or "").strip() or None
        if re.search(r"\bviager\b", f"{(data.get('titles') or {}).get('main')} {description}", re.I):
            details["Viager"] = "oui"
        details = {key: value for key, value in details.items() if value not in (None, "", [], {})}
        images = [url for url in data.get("mediasCDN") or data.get("medias") or [] if isinstance(url, str)]
        negotiator = data.get("negociateur") or {}
        reference = str(data.get("reference") or "") or None
        dpe_class = str(data.get("noteConsoEnergie") or "").upper()
        ges_class = str(data.get("noteEmissionGES") or "").upper()
        yield {
            "id": reference or page_url, "name": (data.get("titles") or {}).get("main"),
            "price": data.get("prixVente"),
            "surface": surface.get("habitable") or surface.get("totale"),
            "land_surface": land,
            "rooms": data.get("nbPiece"), "bedrooms": data.get("nbChambre"),
            "zipcode": location.get("codePostal"), "city": location.get("ville") or locality.get("libelle"),
            "lat": point.get("lat"), "lng": point.get("lon"), "type_hint": type_hint,
            "url": page_url, "body": description,
            "dpe": dpe_class if dpe_class in tuple("ABCDEFG") else None,
            "ges": ges_class if ges_class in tuple("ABCDEFG") else None,
            "seller_name": " ".join(filter(None, [negotiator.get("prenom"), negotiator.get("nom")])) or "Foncia",
            "published_at": data.get("datePublication"),
            "seller_type": "pro",
            "details_json": json.dumps(details, ensure_ascii=False),
            "images_json": json.dumps(images, ensure_ascii=False),
            "image_count": len(images), "reference_annonce": reference,
            "raw_payload": json.dumps(
                {key: value for key, value in data.items() if key not in {"medias", "mediasCDN", "negociateur"}},
                ensure_ascii=False, default=str,
            ),
        }

    def _from_json_ld(self, html: str, page_url: str) -> Iterator[dict]:
        """Repli si l'état Angular disparaît : JSON-LD + prix du titre."""
        type_hint = next((value for key, value in TYPES.items() if f"/{key}/" in page_url), None)
        title = re.search(r"<title>(.*?)</title>", html, re.S)
        price = re.search(r"- (\d+) € -", unescape(title.group(1))) if title else None
        if not type_hint or not price:
            return
        for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
            try:
                node = json.loads(block)
            except json.JSONDecodeError:
                continue
            if not isinstance(node, dict) or str(node.get("@type")).lower() not in {"apartment", "house", "residence", "singlefamilyresidence"}:
                continue
            address = node.get("address") or {}
            reference = re.search(r"/([^/]+)\.htm$", page_url)
            details = {"Année de construction": node.get("yearBuilt"), "Étage": node.get("floorLevel")}
            yield {
                "id": reference.group(1) if reference else page_url, "name": node.get("name"),
                "price": price.group(1), "surface": (node.get("floorSize") or {}).get("value"),
                "rooms": node.get("numberOfRooms"), "zipcode": address.get("postalCode"),
                "city": address.get("addressLocality"), "lat": node.get("latitude"),
                "lng": node.get("longitude"), "type_hint": type_hint, "url": page_url,
                "body": node.get("description"),
                "details_json": json.dumps({k: v for k, v in details.items() if v is not None}, ensure_ascii=False),
                "seller_type": "pro", "images_json": "[]", "image_count": 0,
                "reference_annonce": reference.group(1) if reference else None,
                "raw_payload": json.dumps(node, ensure_ascii=False, default=str),
            }
            return


register(FonciaConnector())
