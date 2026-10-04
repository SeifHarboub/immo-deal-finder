"""Agorastore immobilier : cessions de biens publics (collectivités, État, AGRASC).

Enchères en ligne à prix de départ ; l'état de la vente est sérialisé dans
`React.createElement(FicheProduitApp, {...})`. Une vente close sans enchère est
notée « carence », sinon « adjugé » au dernier prix porté.
"""

from collections.abc import Iterator
import json
import re

from immo.connectors.base import register
from immo.connectors.licitor import (
    EnchereConnector, iso_local, land_surface, make_record, occupation, parse_amount,
    parse_rooms, parse_surface, type_from_text,
)


BASE = "https://www.agorastore-immo.fr"
MARKER = "React.createElement(FicheProduitApp, "


def _app_state(html: str) -> dict | None:
    position = html.find(MARKER)
    if position < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(html[position + len(MARKER):])
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


class AgorastoreConnector(EnchereConnector):
    PARSER_VERSION = 1
    MODE_VENTE = "cession_publique"

    def __init__(self) -> None:
        super().__init__("agorastore", BASE + "/sitemapImmo.xml",
                         lambda url: "/vente-occasion/immobilier/" in url)

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        state = _app_state(html)
        model = (state or {}).get("ficheProduitModel") or {}
        sale = model.get("saleState") or {}
        page = ((model.get("productPageWrapper") or {}).get("productPageModel")) or {}
        product = page.get("product") or {}
        if not product.get("id") or not sale:
            return
        geo = model.get("productGeolocation") or {}
        seller = page.get("seller") or {}
        category = (product.get("category") or {}).get("name") or ""
        name = product.get("name") or sale.get("productName") or ""
        lines = []
        for group in page.get("descriptifs") or []:
            for item in group.get("descriptifs") or []:
                if item.get("value"):
                    lines.append(f"{item.get('descriptifLibelle')} : {item['value'].strip()}")
        body = "\n\n".join(lines)
        initial = parse_amount(sale.get("initialPrice"))
        current = parse_amount(sale.get("currentPrice"))
        bids = int(sale.get("bidsCount") or 0)
        finished = bool(product.get("isSaleFinished")) or sale.get("status") == 2
        if product.get("isCanceled"):
            statut = "retiré"
        elif finished:
            statut = "adjugé" if bids > 0 else "carence"
        else:
            statut = "à venir"
        address = geo.get("address") or geo.get("formattedAddress") or ""
        postal = re.search(r"\b(\d{5})\s+([^,]+)", address)
        city = postal.group(2).strip() if postal else re.sub(r"\s*\(\d+\)\s*$", "", name.rsplit(" - ", 1)[-1]) or None
        kind = type_from_text(category) if category else "autre"
        if kind == "autre":
            kind = type_from_text(name)
        surface = parse_surface(name) or parse_surface(body)
        real_estate = product.get("realEstateInformation") or {}
        documents = [{"titre": doc.get("fileName"), "url": doc.get("url")} for doc in page.get("documents") or []]
        images = [image.get("url") for image in page.get("images") or [] if image.get("url")]
        details = {
            "Statut": statut, "Mise à prix": initial, "Vendeur": seller.get("sellerName"),
            "Occupation": occupation(body), "Nombre d'enchères": bids,
            "Prix courant": current if not finished else None,
            "Début des enchères": iso_local(sale.get("startDate")),
            "Fin des enchères": iso_local(sale.get("endDate")),
            "Frais acheteur (%)": sale.get("tauxFraisAcheteur"),
            "Pas d'enchère": sale.get("bidStep"), "Prix de réserve": sale.get("reservePrice") or None,
            "Dernière visite": iso_local(real_estate.get("lastVisitDate")),
            "Catégorie site": category or None, "Adresse": address or None,
            "Modalités": (product.get("paymentTerms") or "").strip() or None,
            "Documents": documents,
        }
        yield make_record(
            id=product["id"], name=name, price=initial,
            prix_adjuge=current if statut == "adjugé" else None,
            surface=None if kind == "terrain" else surface, land_surface=land_surface(kind, body, surface),
            rooms=parse_rooms(name + "\n" + body), zipcode=postal.group(1) if postal else None, city=city,
            lat=geo.get("latitude"), lng=geo.get("longitude"), type_hint=kind, url=page_url,
            body=body or None, seller_name=seller.get("sellerName"),
            dpe=real_estate.get("dpeRatingValue"), ges=real_estate.get("gesRatingValue"),
            reference_annonce=str(product["id"]), mode_vente=self.MODE_VENTE,
            date_vente=iso_local(sale.get("endDate")), details=details, images=images,
            raw_payload=json.dumps({"saleState": sale, "product": product}, ensure_ascii=False, default=str),
        )


register(AgorastoreConnector())
