from collections.abc import Iterator
from html import unescape
import json
import re

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


FRENCH_DEPARTMENTS = (
    {str(value) for value in range(1, 96) if value != 20}
    | {"2A", "2B", "971", "972", "973", "974", "976"}
)


class OrpiConnector(AgencyJsonLdConnector):
    def __init__(self) -> None:
        super().__init__(
            "orpi", "https://www.orpi.com/sitemap-biens-a-vendre-1.xml",
            lambda url: (
                "/annonce-vente-" in url and "/photos/" not in url
                and bool(re.search(r"-\d{5}(?:-|/)", url))
            ),
        )

    def _sitemap_urls(self, url: str, seen=None):
        for sitemap in (
            "https://www.orpi.com/sitemap-biens-a-vendre-1.xml",
            "https://www.orpi.com/sitemap-biens-a-vendre-2.xml",
        ):
            yield from super()._sitemap_urls(sitemap, seen)

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        description_match = re.search(r'<div class="s-cms u-p">(.*?)</div>', html, re.I | re.S)
        description = None
        if description_match:
            description = re.sub(r"<br\s*/?>", "\n", description_match.group(1), flags=re.I)
            description = unescape(re.sub(r"<[^>]+>", " ", description)).replace("\xa0", " ")
            description = "\n".join(re.sub(r"\s+", " ", line).strip() for line in description.splitlines() if line.strip())
        images = []
        for url in re.findall(r'https://cutjhqvjma\.cloudimg\.io/[^"?]+\.jpg', html, re.I):
            if url not in images:
                images.append(url)
        estate = {}
        estate_match = re.search(r'data-estate="([^"]+)"', html, re.I)
        if estate_match:
            try:
                estate = json.loads(unescape(estate_match.group(1)))
            except (json.JSONDecodeError, TypeError):
                estate = {}
        for match in re.finditer(r'data-eulerian-action="([^"]+)"', html):
            try:
                data = json.loads(unescape(match.group(1)))
            except (json.JSONDecodeError, TypeError):
                continue
            if not data.get("prdref") or data.get("typeTransaction") != "orpi.result.transaction.buy":
                continue
            postal_code = str(data.get("codePostal") or "")
            department = str(data.get("departementId") or "")
            if not re.fullmatch(r"\d{5}", postal_code) or department.upper() not in FRENCH_DEPARTMENTS:
                continue
            labels = "ABCDEFG"
            dpe_value = data.get("dpe")
            ges_value = data.get("ges")
            dpe = labels[dpe_value-1] if isinstance(dpe_value, int) and 1 <= dpe_value <= 7 else dpe_value
            ges = labels[ges_value-1] if isinstance(ges_value, int) and 1 <= ges_value <= 7 else ges_value
            detail_keys = {
                "Année de construction": "anneeConstruction", "Étage": "etage",
                "Niveaux": "nbNiveaux", "Terrasses": "nbTerrasses",
                "Salle(s) de bains": "nbSdb", "Garage(s)": "nbGarage",
                "Parking(s)": "nbParking", "Cave(s)": "nbCaves",
                "Piscine": "piscine", "Plain-pied": "plainPied",
                "Ascenseur": "ascenseur", "Chauffage": "chauffage",
                "Stationnement": "stationnement", "État": "etat",
                "Quartier": "quartier", "Surface du séjour": "surfaceSejour",
                "Neuf / ancien": "neufAncien", "Meublé": "meuble",
                "Accès handicapé": "accesHandicap", "Visite virtuelle": "visiteVirtuelle",
                "Lotissement": "lotissement", "Jardin": "jardin",
                "Balcon(s)": "nbBalcons", "Exclusivité": "exclusivite",
            }
            details = {label: data.get(key) for label, key in detail_keys.items() if data.get(key) not in (None, "", 0, False)}
            rich_keys = {
                "Surface habitable": "liveableSurface", "Surface séjour": "livingroomSurface",
                "Salles d'eau": "nbWashrooms", "Grenier": "attic",
                "Maison d'amis": "guesthouse", "Interphone": "intercom",
                "Digicode": "digitalCode", "Rez-de-chaussée": "streetLevel",
                "Étages du bâtiment": "story", "Étage du bien": "storyLocation",
                "État général": "generalCondition", "DPE consommation": "consumptionValue",
                "GES émissions": "emissionValue", "Date du DPE": "dpeDate",
                "Coût énergétique annuel min": "energyMin",
                "Coût énergétique annuel max": "energyMax", "Honoraires": "fees",
                "Nombre de lots de copropriété": "condoBundleNumber",
                "Charges annuelles de copropriété": "condoCharge",
                "Mise en vente": "onMarketSince", "Mise à jour": "updatedAt",
            }
            details.update({label: estate.get(key) for label, key in rich_keys.items()
                            if estate.get(key) not in (None, "", 0, False)})
            if estate.get("condo") or estate.get("condoCharge"):
                details["Copropriété"] = "Oui"
            if estate.get("blurredness"):
                details["Précision cartographique"] = "Zone approximative publiée par Orpi"
            yield {
                "id": data["prdref"], "name": data.get("prdname"),
                "price": data.get("prdamount"), "surface": data.get("surfaceBien"),
                "land_surface": data.get("surfaceTerrain"), "rooms": data.get("nbPieces"),
                "bedrooms": data.get("nbChambres"), "zipcode": postal_code,
                "city": data.get("nomVille"), "lat": data.get("latitude"),
                "lng": data.get("longitude"), "type_hint": data.get("typeBien"),
                "url": page_url, "body": description, "dpe": dpe, "ges": ges,
                "seller_name": data.get("agenceNom"),
                "published_at": estate.get("onMarketSince") or data.get("dateCreation"),
                "details_json": json.dumps(details, ensure_ascii=False),
                "images_json": json.dumps(images, ensure_ascii=False),
                "image_count": len(images), "reference_annonce": data.get("prdref"),
                "raw_payload": json.dumps(data, ensure_ascii=False, default=str),
            }
            return


register(OrpiConnector())
