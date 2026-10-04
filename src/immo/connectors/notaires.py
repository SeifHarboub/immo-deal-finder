"""Annonces des notaires (immobilier.notaires.fr) via l'API JSON publique.

robots.txt impose Crawl-delay: 10 : une requête toutes les 10 s au minimum.
Un passage complet (~360 pages de 100) prend donc ~1 h ; les passages
intermédiaires sont incrémentaux (tri DATE_MODIFICATION_DESC, arrêt dès que
dateMaj précède le filigrane du dernier passage terminé).
"""
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
import json
import os

from immo.connectors.base import Connector, register
from immo.connectors.bienici import CircuitOpen, PoliteClient, parse_date, read_state, write_state
from immo.schema import Criteria


API_URL = "https://www.immobilier.notaires.fr/pub-services/inotr-www-annonces/v1/annonces"
LIST_PARAMS = {
    "offset": 0, "parPage": 100, "perimetre": 0,
    "typeBiens": "MAI,APP,IMM,TER,LAC", "typeTransactions": "VENTE,VNI,VAE",
    "tri": "DATE_MODIFICATION_DESC",
}
TYPE_HINTS = {
    "MAI": "maison", "APP": "appartement", "IMM": "immeuble", "TER": "terrain",
    "LAC": "local commercial", "AGR": "autre",
}
UNKNOWN = {None, "", "INCONNU", "Z"}


def _known(value):
    return None if value in UNKNOWN else value


def sale_mode(item: dict) -> str:
    kind = item.get("typeTransaction")
    if kind == "VNI":
        return "vente_interactive"
    if kind == "VAE":
        return "enchere_judiciaire" if item.get("typeAdjudication") == "JUDICIAIRE" else "enchere_notariale"
    return "gre_a_gre"


def _find_keys(value, names: set[str]) -> dict:
    """Recherche récursive (coordonnées du détail, rangées à part)."""
    found = {}
    if isinstance(value, dict):
        for key, item in value.items():
            if key in names and isinstance(item, (int, float)):
                found[key] = item
            elif isinstance(item, (dict, list)):
                found.update({k: v for k, v in _find_keys(item, names).items() if k not in found})
    elif isinstance(value, list):
        for item in value:
            found.update({k: v for k, v in _find_keys(item, names).items() if k not in found})
    return found


def _detail_fields(detail: dict) -> dict:
    """Champs utiles de la fiche détaillée (DPE, taxe foncière, photos…)."""
    sale = detail.get("vente") or {}
    good = detail.get("bien") or {}
    specific = next((value for key, value in good.items() if isinstance(value, dict) and key != "id"), {})
    description = next((item.get("descLongue") for item in sale.get("descriptions") or []
                        if item.get("langue") == "fr" and item.get("descLongue")), None)
    photos = [
        media.get("urlHighestResolution") or (media.get("vga") or {}).get("url")
        for media in sale.get("multimedias") or []
        if str(media.get("type", "")).startswith("image")
    ]
    return {
        "description": description, "photos": [url for url in photos if url],
        "dpe": _known(specific.get("consommationClasse")), "ges": _known(specific.get("emissionGesClasse")),
        "details": {
            "Taxe foncière": specific.get("taxeFonciere"),
            "Charges annuelles de copropriété": specific.get("chargesCopropriete") or specific.get("montantChargesAnnuelles"),
            "DPE consommation": specific.get("consommationValeur"),
            "GES émissions": specific.get("emissionGesValeur"),
            "État": _known(specific.get("etat")), "Chauffage": _known(specific.get("chauffage")),
            "Situation locative": _known(specific.get("situationLocative")),
            "Exclusivité": "Oui" if sale.get("exclusivite") == "OUI" else None,
        },
        # Coordonnées marquées non publiques : conservées seulement dans
        # raw_payload pour comparaison interne, jamais exposées en lat/lng.
        "internal_coordinates": _find_keys(detail, {"latitude", "longitude", "lat", "lng", "lon"}),
    }


def map_annonce(item: dict, detail: dict | None = None) -> dict:
    mode = sale_mode(item)
    auction = mode != "gre_a_gre"
    viager = item.get("viager") not in (None, "NON", "INCONNU")
    price = item.get("prixAffiche") if auction else (item.get("prixTotal") or item.get("prixAffiche"))
    sale_date = item.get("dateFinEncheres") or item.get("seanceDate")
    details = {
        "Code INSEE": item.get("inseeCommune"), "Département": item.get("departementNom"),
        "Quartier": item.get("quartierNom"), "Neuf / ancien": _known(item.get("ancienNeuf")),
        "Office notarial (CRPCEN)": item.get("crpcen"),
        "Date de modification": item.get("dateMaj"), "Date du DPE": item.get("dateRealisationDpe"),
        "Viager": "oui" if viager else None,
    }
    if auction:
        details.update({
            "Mise à prix": item.get("prixAffiche"),
            "Première offre possible": item.get("premiereOffrePossible"),
            "Consignation": item.get("consignation"),
            "Début des enchères": item.get("dateDebutEncheres"),
            "Fin des enchères": item.get("dateFinEncheres"),
            "Date de séance": item.get("seanceDate"),
            "Type d'adjudication": _known(item.get("typeAdjudication")),
            "Origine judiciaire": _known(item.get("origineJudiciaire")),
            "Mode d'enchères": _known(item.get("modeVente")) or _known(item.get("systemeEncheres")),
            "Nature de la mise à prix": _known(item.get("natureMiseAPrix")),
            "Bien vendu": _known(item.get("bienVendu")), "Vente reportée": _known(item.get("venteReportee")),
            "Prix d'adjudication": item.get("prixAdjudication"),
        })
    else:
        details.update({
            "Prix hors émoluments": item.get("prixAffiche"),
            "Émoluments de négociation": item.get("emoluments"),
            "Émoluments (%)": item.get("pourcentageEmoluments"),
            "Émoluments à la charge": _known(item.get("redevableEmoluments")),
        })
    body = item.get("descriptionFr")
    images = [item["urlPhotoPrincipale"]] if item.get("urlPhotoPrincipale") else []
    dpe = ges = None
    payload = {"annonce": item}
    if detail:
        extra = _detail_fields(detail)
        body = extra["description"] or body
        images = extra["photos"] or images
        dpe, ges = extra["dpe"], extra["ges"]
        details.update(extra["details"])
        payload["detail"] = detail
        if extra["internal_coordinates"]:
            payload["coordonnees_internes_non_publiques"] = extra["internal_coordinates"]
    details = {key: value for key, value in details.items() if value not in (None, "", [], {}, 0)}
    published = parse_date(item.get("dateCreation"))
    city = item.get("communeNom") or item.get("localiteNom")
    kind = TYPE_HINTS.get(item.get("typeBien"), "autre")
    return {
        "id": str(item.get("annonceId")), "name": f"{kind.capitalize()} à {city}" if city else kind,
        "price": price, "surface": item.get("surface"), "land_surface": item.get("surfaceTerrain"),
        "rooms": item.get("nbPieces"), "bedrooms": item.get("nbChambres"),
        "zipcode": item.get("codePostal"), "city": city, "lat": None, "lng": None,
        "type_hint": item.get("typeBien"), "url": item.get("urlDetailAnnonceFr"),
        "body": body, "seller_name": None, "dpe": dpe, "ges": ges,
        "image_count": max(len(images), int(item.get("nbPhoto") or 0)),
        "published_at": published.isoformat() if published else None,
        "details_json": json.dumps(details, ensure_ascii=False),
        "images_json": json.dumps(images, ensure_ascii=False),
        "reference_annonce": item.get("reference"),
        "raw_payload": json.dumps(payload, ensure_ascii=False, default=str),
        "mode_vente": mode, "date_vente": sale_date,
        "prix_adjuge": item.get("prixAdjudication"),
        "categorie": "vente",
    }


class NotairesConnector(Connector):
    source = "notaires"

    def _known_ids(self) -> set[str]:
        try:
            from immo.warehouse import connect
            with connect() as con:
                exists = con.execute(
                    "SELECT count(*) FROM information_schema.tables WHERE table_name='raw_notaires'"
                ).fetchone()[0]
                if not exists:
                    return set()
                return {row[0] for row in con.execute(
                    "SELECT DISTINCT CAST(id AS VARCHAR) FROM raw_notaires WHERE id IS NOT NULL"
                ).fetchall()}
        except Exception:
            return set()

    def fetch(self, c: Criteria) -> Iterator[dict]:
        # Crawl-delay: 10 ; un réglage plus bas est ramené à 10 s.
        client = PoliteClient(self.source, 10, 12, headers={"Accept": "application/json"})
        client.min_delay = max(10.0, client.min_delay)
        client.max_delay = max(client.min_delay, client.max_delay)
        started = datetime.now(timezone.utc)
        max_pages = c.max_pages or 0
        fetch_details = os.getenv("NOTAIRES_FETCH_DETAILS", "false").lower() in {"1", "true", "oui", "yes"}
        known = self._known_ids() if fetch_details else set()
        last_full = read_state(self.source, "full_scan")
        watermark = read_state(self.source, "watermark")
        full = (last_full is None or watermark is None or last_full.replace(tzinfo=timezone.utc)
                < started - timedelta(days=float(os.getenv("NOTAIRES_FULL_SCAN_DAYS", "2"))))
        since = None if full else watermark.replace(tzinfo=timezone.utc) - timedelta(minutes=30)
        page = pages = count = details = missed = 0
        total_pages = None
        circuit = reached = False
        try:
            while total_pages is None or page < total_pages:
                if max_pages and pages >= max_pages:
                    break
                page += 1
                payload = client.get_json(API_URL, {**LIST_PARAMS, "page": page})
                pages += 1
                if not payload:
                    missed += 1
                    continue
                total_pages = int(payload.get("nbPages") or 0)
                items = payload.get("annonceResumeDto") or []
                if not items:
                    break
                for item in items:
                    modified = parse_date(item.get("dateMaj"))
                    if since and modified and modified < since:
                        reached = True
                        break
                    if not item.get("annonceId"):
                        continue
                    detail = None
                    if fetch_details and str(item["annonceId"]) not in known:
                        detail = client.get_json(f"{API_URL}/{item['annonceId']}")
                        details += 1
                    count += 1
                    yield map_annonce(item, detail)
                if reached:
                    break
        except CircuitOpen:
            circuit = True
        complete = not circuit and not max_pages and not missed
        if complete:
            write_state(self.source, "watermark", started)
            if full:
                write_state(self.source, "full_scan", started)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        print(
            f"Diagnostic {self.source}: mode {'complet' if full else 'incrémental'}, "
            f"{client.requests} requêtes, {pages}/{total_pages or '?'} pages, {details} fiches, "
            f"{count} annonces, {client.failures} échecs, {elapsed:.1f}s"
            f"{' (coupe-circuit ouvert)' if circuit else ''}"
            f"{'' if complete else ' (passage partiel, état non avancé)'}.",
            flush=True,
        )


register(NotairesConnector())
