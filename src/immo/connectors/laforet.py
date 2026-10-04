from collections.abc import Iterator
from html import unescape
import json
import re
from urllib.parse import urljoin

import requests

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


class LaforetConnector(AgencyJsonLdConnector):
    def __init__(self) -> None:
        super().__init__(
            "laforet", "https://www.laforet.com/storage/sitemaps/produits.xml",
            lambda url: "/acheter/" in url,
        )

    def _fetch_detail(self, url: str) -> tuple[str, str] | None:
        """Récupère aussi le SVG DPE chargé séparément par Laforêt.

        La page principale ne contient que ``/ajax/properties/{id}/dpe``. Sans
        cette seconde réponse, la classe et les consommations restent invisibles
        au parseur alors qu'elles sont affichées dans le navigateur.
        """
        result = super()._fetch_detail(url)
        if result is None:
            return None
        html, final_url = result
        dpe_path = re.search(r'<img[^>]+src="([^"]+/dpe)"', html, re.I)
        if not dpe_path:
            return result
        try:
            response = self._detail_session().get(
                urljoin(final_url, unescape(dpe_path.group(1))), timeout=15,
            )
            response.raise_for_status()
            html += f"\n<!-- IMMO_DPE_SVG -->\n{response.text}"
        except requests.RequestException:
            # Les autres caractéristiques restent exploitables si le SVG échoue.
            pass
        return html, final_url

    @staticmethod
    def _features(html: str) -> tuple[dict, float | None]:
        section = re.search(
            r'<section[^>]+id="section-features"[^>]*>(.*?)</section>',
            html, re.I | re.S,
        )
        if not section:
            return {}, None
        labels = []
        for raw_label in re.findall(r"<span[^>]*>([^<]+)</span>", section.group(1), re.I):
            label = re.sub(r"\s+", " ", unescape(raw_label)).strip()
            if label and label not in labels:
                labels.append(label)

        details: dict[str, str | int | float] = {}
        land_surface = None
        for label in labels:
            colon = re.match(r"([^:]+):\s*([\d.,]+)\s*(m²)?$", label, re.I)
            if colon:
                key = colon.group(1).strip().replace("Surf. séj", "Surface séjour")
                key = key.replace("Surf. terrain", "Surface terrain").replace("Surface", "Surface", 1)
                value = float(colon.group(2).replace(" ", "").replace(",", "."))
                details[key] = value
                if key == "Surface terrain":
                    land_surface = value
                continue
            count = re.match(r"(\d+)\s+(pièces|chbres|chambres|salles? de bain|salles? d'eau)$", label, re.I)
            if count:
                names = {
                    "pièces": "Pièces", "chbres": "Chambres", "chambres": "Chambres",
                    "salle de bain": "Salles de bain", "salles de bain": "Salles de bain",
                    "salle d'eau": "Salles d'eau", "salles d'eau": "Salles d'eau",
                }
                details[names[count.group(2).lower()]] = int(count.group(1))
                continue
            heating = re.match(r"Chauffage\s+(.+)", label, re.I)
            if heating:
                details["Chauffage"] = heating.group(1).strip()
            elif re.match(r"Cuisine\s+", label, re.I):
                details["Cuisine"] = re.sub(r"^Cuisine\s+", "", label, flags=re.I)
            else:
                details[label] = "Oui"
        return details, land_surface

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        match = re.search(r'dataLayer\.push\(\.\.\.(\[.*?\])\);', html, re.S)
        if not match:
            return
        try:
            events = json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            return
        data = next((item for item in events if item.get("event") == "viewItem"), None)
        if not data or data.get("transactionType") != "acheter":
            return
        title = data.get("itemName")
        descriptions = re.findall(r'data-description="(.*?)"\s+(?:data-|type=)', html, re.I | re.S)
        body = unescape(max(descriptions, key=len)) if descriptions else None
        if body:
            body = re.sub(r"<br\s*/?>|&lt;br\s*/?&gt;", "\n", body, flags=re.I)
            body = unescape(re.sub(r"<[^>]+>", " ", body)).replace("\xa0", " ")
            body = "\n".join(re.sub(r"\s+", " ", line).strip() for line in body.splitlines() if line.strip())
        images = []
        for image in re.findall(r'https://[^"\s]+/catalog/images/[^"\s]+\.jpg', html, re.I):
            image = unescape(image)
            if image not in images:
                images.append(image)
        bedrooms_match = re.search(r"(\d+)\s*chambres?", str(data.get("itemCriteria") or ""), re.I)
        details, land_surface = self._features(html)
        details.update({
            label: "Oui" for label, key in {
                "Ascenseur":"itemAscenseur", "Piscine":"itemPiscine", "Cave":"itemCave",
                "Sous-sol":"itemSousSol", "Balcon":"itemBalcon", "Parking":"itemParking",
                "Garage":"itemGarage", "Cheminée":"itemCheminee", "Prestige":"itemPrestige",
                "Viager":"itemViager",
            }.items() if data.get(key) and label not in details
        })
        energy_costs = re.search(
            r"dépenses annuelles d'énergie.*?entre\s*([\d\s .,]+)\s*€\s*et\s*([\d\s .,]+)\s*€",
            html, re.I | re.S,
        )
        if energy_costs:
            details["Dépenses énergie annuelles minimales"] = float(
                re.sub(r"[^\d,]", "", energy_costs.group(1)).replace(",", ".")
            )
            details["Dépenses énergie annuelles maximales"] = float(
                re.sub(r"[^\d,]", "", energy_costs.group(2)).replace(",", ".")
            )
        agency_ref = re.search(r"Référence Agence\s*:\s*([^<\s]+)", html, re.I)
        if agency_ref:
            details["Référence agence"] = agency_ref.group(1)
        if re.search(r"Honoraires à la charge du vendeur", html, re.I):
            details["Honoraires"] = "Charge vendeur"

        dpe_match = re.search(r'id="DPE_([A-G])"', html, re.I)
        if not dpe_match:
            dpe_match = re.search(r"\bDPE\s+([A-G])\b", body or "", re.I)
        ges_match = re.search(r'id="GES_([A-G])"', html, re.I)
        map_center = re.search(
            r'data-map-center-value="\{&quot;lat&quot;:([\d.-]+),&quot;lng&quot;:([\d.-]+)\}"',
            html, re.I,
        )
        latitude = float(map_center.group(1)) if map_center else None
        longitude = float(map_center.group(2)) if map_center else None
        if map_center:
            details["Coordonnées carte source"] = f"{latitude:.6f}, {longitude:.6f}"
        dpe_value = re.search(r'id="data_dpe"[^>]*>.*?<tspan[^>]*>(\d+)', html, re.I | re.S)
        ges_value = re.search(r'id="data_ges"[^>]*>.*?<tspan[^>]*>(\d+)', html, re.I | re.S)
        if dpe_value:
            details["Consommation énergétique"] = f"{dpe_value.group(1)} kWhEP/m²/an"
        if ges_value:
            details["Émissions GES"] = f"{ges_value.group(1)} kgCO₂/m²/an"
        yield {
            "id": data.get("itemId") or page_url, "name": title,
            "price": data.get("itemPrice"), "surface": data.get("itemSize"),
            "land_surface": land_surface, "rooms": data.get("itemRoomsNb"),
            "bedrooms": bedrooms_match.group(1) if bedrooms_match else None, "zipcode": data.get("itemZipcode"),
            "city": data.get("itemCity"), "lat": latitude, "lng": longitude,
            "type_hint": data.get("itemType"), "url": page_url,
            "body": body, "dpe": dpe_match.group(1).upper() if dpe_match else None,
            "ges": ges_match.group(1).upper() if ges_match else None,
            "details_json": json.dumps(details, ensure_ascii=False),
            "images_json": json.dumps(images, ensure_ascii=False),
            "image_count": len(images), "reference_annonce": str(data.get("itemId") or ""),
            "raw_payload": json.dumps(data, ensure_ascii=False, default=str),
        }


register(LaforetConnector())
