"""encheresimmobilieres.fr : ventes judiciaires du Grand Sud (Tribune Côte d'Azur).

Sources de données par ordre de priorité : l'objet « vente » de la charge Next.js
(prix, adjudication, lots, tribunal, avocat), le JSON-LD Product, puis le texte des
pages d'archives (« Résultat : 107 000 € »).
"""

from collections.abc import Iterator
import json
import re

from immo.connectors.base import register
from immo.connectors.licitor import (
    EnchereConnector, cle_enchere, clean_text, iso_local, land_surface, make_record, occupation,
    parse_amount, parse_fr_date, parse_rooms, parse_surface, plain, type_from_text,
)


BASE = "https://encheresimmobilieres.fr"


def _first(pattern: str, text: str, flags=re.S | re.I) -> str | None:
    match = re.search(pattern, text or "", flags)
    return match.group(1) if match else None


def _vente_object(html: str) -> dict | None:
    """Objet « vente » sérialisé dans les fragments self.__next_f (RSC)."""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html, re.S)
    if not chunks:
        return None
    try:
        payload = "".join(json.loads(f'"{chunk}"') for chunk in chunks)
    except json.JSONDecodeError:
        return None
    position = payload.find('"typeVente"')
    while position > 0:
        start = payload.rfind('{"id":', 0, position)
        try:
            value, _ = json.JSONDecoder().raw_decode(payload[start:])
        except (json.JSONDecodeError, ValueError):
            value = None
        if isinstance(value, dict) and "titre" in value and "prix" in value:
            return value
        position = payload.find('"typeVente"', position + 1)
    return None


def _resultat(text, montant=None) -> tuple[str, float | None]:
    if montant:
        return "adjugé", float(montant)
    source = plain(text)
    amount = parse_amount(source) if re.search(r"\d", source) else None
    if amount and amount > 100:
        return "adjugé", amount
    if re.search(r"carence|desert|non adjuge|invendu", source):
        return "carence", None
    if re.search(r"retir|non ?requise|radi|annul|suspend", source):
        return "retiré", None
    if "non communique" in source:
        return "non communiqué", None
    if re.search(r"report|renvoi", source):
        return "reporté", None
    return "à venir", None


class EncheresImmoConnector(EnchereConnector):
    PARSER_VERSION = 1

    def __init__(self) -> None:
        super().__init__("encheresimmo", BASE + "/ventes/sitemap/0.xml",
                         lambda url: re.search(r"/ventes/\d+-", url) is not None)

    def _map_node(self, node: dict, page_url: str) -> dict | None:
        mapped = super()._map_node(node, page_url)
        if mapped:
            mapped["priceValidUntil"] = (node.get("offers") or {}).get("priceValidUntil")
        return mapped

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        number = _first(r"/ventes/(\d+)-", page_url)
        if not number:
            return
        vente = _vente_object(html) or {}
        ld = next(iter(super().parse_page(html, page_url)), {})
        text = clean_text(re.sub(r"<(script|style|svg)\b.*?</\1>", " ", html, flags=re.S))
        title = vente.get("titre") or ld.get("name") or clean_text(_first(r"<h1[^>]*>(.*?)</h1>", html))
        if "Descriptif du bien" in text:
            body = text.split("Descriptif du bien", 1)[1].split("Informations sommaires", 1)[0]
        else:
            body = text.split("Date de la vente", 1)[-1].split("Téléchargez vos prochaines ventes", 1)[0]
        body = re.sub(r"\n{2,}", "\n", body).strip()[:8000]
        complement = clean_text(vente.get("complement"))
        tgi = vente.get("tgi") or {}
        tribunal = tgi.get("nom") or (
            "Tribunal Judiciaire de " + _first(r"Tribunal Judiciaire de\s+([^\n,]+)", text).strip().title()
            if _first(r"Tribunal Judiciaire de\s+([^\n,]+)", text) else None)
        avocat = vente.get("avocat") or {}
        lawyer = avocat.get("nom") or clean_text(_first(r"Avocat\s*:\s*\n?\s*([^\n]+)", text)) or None
        date_vente = (iso_local(vente.get("dateVente")) or iso_local(ld.get("priceValidUntil"))
                      or parse_fr_date(_first(r"Date de la vente\s*:\s*([^\n]+)", text)))
        mise_text = _first(r'name="description" content="[^"]*?mise à prix ([\d\s.,  ]+)\s*€', html) \
            or _first(r"MISE À PRIX\s*:\s*([\d\s.,  ]+)\s*€", text)
        mise = parse_amount(vente.get("prix")) or parse_amount(ld.get("price")) or parse_amount(mise_text)
        statut, adjuge = _resultat(
            vente.get("resultatAdjudication") or _first(r"R[ée]sultat(?:\s*:\s*|\s*\n\s*)([^\n]+)", text) or "",
            vente.get("prixAdjudication"))
        if statut == "à venir" and vente.get("statut") == "resultat":
            statut = "non communiqué"
        city = vente.get("ville") or ld.get("city") or clean_text(
            _first(r"Retour aux archives\s*\n+\s*([^\n]+)", text)) or None
        zipcode = vente.get("codePostal") or ld.get("zipcode") or _first(r"\((\d{5})\)", body)
        labels = [item.get("type", {}).get("label") for item in vente.get("typesBiens") or [] if item.get("type")]
        visits = "; ".join(
            f"{iso_local(item.get('start'))} → {iso_local(item.get('end'))} {item.get('complement') or ''}".strip()
            for item in vente.get("visites") or []) or None
        images = [f"{BASE}/images/photos/{photo['name']}" for photo in vente.get("photos") or [] if photo.get("name")]
        if not images and isinstance(ld, dict):
            images = re.findall(r"https://encheresimmobilieres\.fr/images/photos/[^\"'\s]+", html)[:20]
        base_details = {
            "Tribunal": tribunal, "Avocat": lawyer, "Visites": visits,
            "Type de vente": vente.get("typeVente"), "Référence greffe": (vente.get("ccv") or "").strip() or None,
            "Documents": [doc.get("name") for doc in vente.get("documents") or []],
            "Adresse": vente.get("adresse"), "Département": vente.get("departement"),
        }
        lots = [lot for lot in vente.get("lots") or [] if lot.get("prix")] or [None]
        for index, lot in enumerate(lots, start=1):
            lot_title = clean_text(lot.get("designation")).lstrip("• ") if lot else title
            lot_mise = parse_amount(lot.get("prix")) if lot else mise
            if lot_mise is None:
                continue
            lot_statut, lot_adjuge = (_resultat(lot.get("resultatAdjudication") or "", lot.get("adjudication"))
                                      if lot else (statut, adjuge))
            kind = type_from_text(lot_title)
            if kind == "autre" and labels:
                kind = type_from_text(labels[0])
            surface = parse_surface(lot_title) or (parse_surface(body) if len(lots) == 1 else None)
            details = dict(base_details)
            details.update({
                "Statut": lot_statut, "Mise à prix": lot_mise,
                "Occupation": occupation("\n".join([lot_title, complement, body])),
                "Lot": f"{index}/{len(lots)}" if len(lots) > 1 else None,
                "Faculté de baisse": (lot.get("complement") if lot else None) or None,
                "Catégorie site": labels[0] if labels else None,
                "Clé enchère": cle_enchere(tribunal, date_vente, city, lot_mise),
            })
            rooms = _first(r"\b(\d{1,2})\s?P\b", lot_title, re.S)
            yield make_record(
                id=number if len(lots) == 1 else f"{number}-{index}", name=lot_title, price=lot_mise,
                prix_adjuge=lot_adjuge, surface=None if kind == "terrain" else surface,
                land_surface=land_surface(kind, body, surface), rooms=rooms or parse_rooms(lot_title),
                zipcode=zipcode, city=city, lat=vente.get("latitude") or ld.get("lat"),
                lng=vente.get("longitude") or ld.get("lng"), type_hint=kind, url=page_url,
                body="\n\n".join(part for part in (complement, body) if part) or None,
                seller_name=lawyer, published_at=iso_local(vente.get("dateParution")),
                reference_annonce=number, mode_vente=self.MODE_VENTE, date_vente=date_vente,
                details=details, images=images,
                raw_payload=json.dumps({key: value for key, value in vente.items()
                                        if key not in {"photos", "complement", "entete"}},
                                       ensure_ascii=False, default=str) if vente else None,
            )


register(EncheresImmoConnector())
