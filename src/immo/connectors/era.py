from collections.abc import Iterator
from html import unescape
import json
import re
import unicodedata

from immo.connectors.agency import AgencyJsonLdConnector
from immo.connectors.base import register


TYPES = {
    "maison": "maison", "appartement": "appartement", "immeuble": "immeuble",
    "terrain": "terrain", "local commercial": "local_commercial",
    "local professionnel": "local_commercial", "fonds de commerce": "fonds_commerce",
    "bureau": "bureau", "bureaux": "bureau",
}


def _plain(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text).strip().lower()


def _float(value) -> float | None:
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number or None


class EraConnector(AgencyJsonLdConnector):
    """ERA Immobilier : la fiche Angular embarque la réponse API dans `ng-state`.

    L'API api.eraimmobilier.com est exclue par robots.txt : seule la page HTML
    publique est téléchargée, jamais l'API elle-même.
    """

    def __init__(self) -> None:
        super().__init__(
            "era", "https://www.eraimmobilier.com/sitemap/sitemap_silo_achat_biens.xml",
            lambda url: bool(re.search(r"/annonces/\d+/?$", url)),
        )

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        match = re.search(r'<script id="ng-state" type="application/json">(.*?)</script>', html, re.S)
        if not match:
            return
        state = json.loads(match.group(1))
        data = next((
            value.get("data") for key, value in state.items()
            if re.search(r"/api/v2/annonces/\d+$", key) and isinstance(value, dict)
        ), None)
        if not isinstance(data, dict) or _plain(data.get("type_annonce")) != "vente":
            return
        kind = _plain(data.get("type_bien") or (data.get("typeBien") or {}).get("libelle"))
        if data.get("critere_neuf") or "programme" in kind or "neuf" in kind:
            return
        type_hint = TYPES.get(kind)
        if not type_hint and ("parking" in kind or "garage" in kind):
            return
        type_hint = type_hint or "autre"
        description = data.get("descriptif") or ""
        description = unescape(re.sub(r"<br\s*/?>", "\n", description, flags=re.I))
        description = re.sub(r"<[^>]+>", " ", description).strip() or None
        images = list((data.get("images") or {}).get("big") or []) or list(data.get("photo") or [])
        geoloc = data.get("geoloc") or {}
        agency = data.get("agence") or {}
        advisor = data.get("effectif") or {}
        copro = str(data.get("copro") or "0") not in {"0", "", "None"}
        flags = {
            "Ascenseur": "ascenseur", "Accès handicapé": "acces_handicape", "Vue mer": "vue_mer",
            "Exclusivité": "mandat_exclusif", "Coup de cœur": "coup_coeur", "Prestige": "prestige",
            "Balcon": "critere_balcon", "Terrasse": "critere_terrasse", "Garage": "critere_garage",
            "Cave": "critere_cave", "Piscine": "critere_piscine", "Jardin": "critere_jardin",
        }
        # Les critères ERA valent 0/1/2 : seul 1 est un « oui » certain.
        details = {label: "oui" for label, key in flags.items() if str(data.get(key)) in {"1", "True"}}
        details.update({
            "Référence agence": data.get("reference"), "Identifiant ERA": data.get("era_id"),
            "Code INSEE": data.get("code_insee"),
            "Département": (data.get("geo_departement") or {}).get("nom"),
            "Libellé": data.get("libelle"), "État": data.get("etat"),
            "Étage": data.get("etage") or None,
            "Année de construction": data.get("annee_construction") or None,
            "Salles de bain": data.get("nb_salle_bain") or None,
            "Salles d'eau": data.get("nb_salle_eau") or None,
            "Balcons": data.get("nb_balcon") or None,
            "Parkings": data.get("nb_parkings") or None, "Box": data.get("nb_boxes") or None,
            "Prix net vendeur": _float(data.get("prix_net_vendeur")),
            "Honoraires (%)": _float(data.get("honoraires")),
            "Honoraires à la charge": {1: "vendeur", 2: "acquéreur"}.get(data.get("commission_payeur")),
            "Copropriété": "oui" if copro else None,
            "Nombre de lots": data.get("copro_nb_logements") if copro else None,
            "Procédure de copropriété en cours": data.get("copro_difficulte") if copro else None,
            "Charges annuelles de copropriété": _float(data.get("copro_charges")) if copro else None,
            "DPE consommation": _float(data.get("emission_dpe")),
            "GES émissions": _float(data.get("emission_ges")),
            "Date du DPE": data.get("dpe_date"),
            "Coût énergétique annuel min": _float(data.get("montant_energie_min") or data.get("dpe_min")),
            "Coût énergétique annuel max": _float(data.get("montant_energie_max") or data.get("dpe_max")),
            # Code de statut ERA brut (0 = disponible) : signification des autres non publiée.
            "Statut ERA": data.get("statut") or None,
            "Visite virtuelle": data.get("visite_virtuelle_url") or None,
            "Agence": agency.get("enseigne"),
            "Viager": "oui" if re.search(r"\bviager\b", f"{data.get('libelle')} {description}", re.I) else None,
        })
        if str(data.get("precision_geoloc") or "1") != "1":
            details["Précision cartographique"] = "approximative"
        details = {key: value for key, value in details.items() if value not in (None, "", [], {})}
        reference = str(data.get("id") or "") or None
        dpe = str(data.get("bilan_dpe") or "").upper()
        ges = str(data.get("bilan_ges") or "").upper()
        seller = agency.get("enseigne") or " ".join(
            filter(None, [advisor.get("prenom"), advisor.get("nom")])
        ) or None
        yield {
            "id": reference or page_url,
            "name": data.get("libelle"), "price": data.get("prix"),
            "surface": data.get("surface_habitable") or None,
            "land_surface": data.get("surface_terrain") or None,
            "rooms": data.get("nb_pieces") or None, "bedrooms": data.get("nb_chambres"),
            "zipcode": data.get("code_postal"),
            "city": str(data.get("ville") or "").title() or None,
            "lat": geoloc.get("lat"), "lng": geoloc.get("lng"), "type_hint": type_hint,
            "url": page_url, "body": description,
            "dpe": dpe if dpe in tuple("ABCDEFG") else None,
            "ges": ges if ges in tuple("ABCDEFG") else None,
            "seller_name": seller, "published_at": data.get("date_publication"),
            "seller_type": "pro",
            "details_json": json.dumps(details, ensure_ascii=False),
            "images_json": json.dumps(images, ensure_ascii=False),
            "image_count": len(images), "reference_annonce": data.get("reference") or reference,
            "raw_payload": json.dumps(
                {key: value for key, value in data.items()
                 if key not in {"agence", "effectif", "statistiques", "images", "photo", "geo_ville"}},
                ensure_ascii=False, default=str,
            ),
        }


register(EraConnector())
