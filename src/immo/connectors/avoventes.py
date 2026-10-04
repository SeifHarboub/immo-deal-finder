"""Avoventes : ventes judiciaires publiées par les cabinets d'avocats.

Ventes à venir : une seule page (/recherche/toutes). Ventes passées : 100 par
page, triées par date décroissante ; rattrapage progressif avec curseur.
"""

from collections.abc import Iterator
import json
import re
import threading

from immo.connectors.base import register
from immo.connectors.licitor import (
    EnchereConnector, cle_enchere, clean_text, land_surface, make_record, occupation, parse_amount,
    parse_fr_date, parse_surface, plain, round_robin, type_from_text,
)


BASE = "https://avoventes.fr"
_CARD = re.compile(r'data-link="(https://avoventes\.fr/enchere/[^"]+)"')


def _first(pattern: str, text: str, flags=re.S | re.I) -> str | None:
    match = re.search(pattern, text or "", flags)
    return match.group(1) if match else None


def _tribunal(text: str) -> str | None:
    match = re.search(
        r"tribunal (?:judiciaire|de grande instance)\s+(?:de |d'|d’|du )\s*([A-Za-zÀ-ÿ' -]{2,40}?)"
        r"(?=\s*[,.;:()\n]|\s+(?:le|à|a|sous|au|en|où|du|section|pour|par|et)\b|$)", text or "", re.I)
    return f"Tribunal judiciaire de {match.group(1).strip().title()}" if match else None


class AvoventesConnector(EnchereConnector):
    PARSER_VERSION = 1

    def __init__(self) -> None:
        super().__init__("avoventes", BASE + "/recherche/toutes")
        self._listing_status: dict[str, str] = {}
        self._status_lock = threading.Lock()

    def _extract(self, html: str) -> tuple[list[str], int | None]:
        """URL des fiches + statut affiché sur la carte (ruban ou adjudication)."""
        urls = []
        for chunk in html.split('data-link="')[1:]:
            url = chunk.split('"', 1)[0]
            if not url.startswith(BASE + "/enchere/"):
                continue
            urls.append(url)
            card = plain(clean_text(chunk[:5000].split('data-link="', 1)[0]))
            ribbon = _first(r'ribbon-inner">([^<]+)<', chunk[:5000])
            status = None
            if ribbon:
                ribbon = plain(ribbon)
                status = "carence" if "desert" in ribbon or "carence" in ribbon else "retiré" if re.search(
                    r"retir|non requise|annul|suspend", ribbon) else None
            elif re.search(r"adjuge\s*:", card):
                status = "adjugé"
            if status:
                with self._status_lock:
                    self._listing_status[url] = status
        pages = [int(value) for value in re.findall(r"ventes-passees\?page=(\d+)", html)]
        return list(dict.fromkeys(urls)), max(pages) if pages else None

    def _upcoming(self) -> Iterator[str]:
        listing = self._listing_page(BASE + "/recherche/toutes", self._extract)
        yield from (listing[0] if listing else [])

    def _candidate_urls(self) -> Iterator[str]:
        url_for = lambda page: f"{BASE}/ventes-passees?page={page}&sort=date&order=desc"  # noqa: E731
        yield from round_robin(
            self._upcoming(),
            self._fresh_pages(url_for, self._extract, int(self._env("AVOVENTES_FRESH_MAX_PAGES", 5))),
            self._backfill_pages("ventes-passees", url_for, self._extract,
                                 int(self._env("AVOVENTES_HISTORY_PAGES_PER_RUN", 3))),
        )

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        title = clean_text(_first(r"<h1[^>]*>(.*?)</h1>", html))
        if "/enchere/" not in page_url or not title:
            return
        head = html[html.find("<h1"):html.find("propos du bien")] if "propos du bien" in html else html
        head_text = re.sub(r"\s+", " ", clean_text(re.sub(r"<select.*?</select>", " ", head, flags=re.S)))
        mise = parse_amount(_first(r"Mise à prix\s*:\s*([\d\s.,  ]+)\s*(?:€|euros)", head_text))
        if mise is None:
            return
        badges = [clean_text(value) for value in re.findall(
            r'<span class=["\']badge badge-secondary["\'][^>]*>(.*?)</span>', html, re.S)]
        adjuge = parse_amount(_first(r"(?:Adjug[ée] à|Adjug[ée]\s*:|Adjudication\s*:)\s*([\d\s.,  ]+)"
                                     r"\s*(?:€|euros)", head_text))
        statut = "adjugé" if adjuge else "à venir"
        if re.search(r"Adjudication\s*:\s*(?:ench[eè]res?\s+d[ée]sertes?|carence)", head_text, re.I):
            statut = "carence"
        statut = self._listing_status.get(page_url, statut) if statut == "à venir" else statut
        date_vente = parse_fr_date(_first(r"\bVente\s+(\d{1,2}\s+\w+\s+\d{4}(?:\s+à\s+\d{1,2}h\d{0,2})?)", head_text))
        stats = {plain(label): parse_amount(value) for value, label in re.findall(
            r'<span class="font-weight-bold h4 mb-0">([^<]+)</span>\s*<div class="small text-muted">([^<]+)</div>',
            html)}
        description = clean_text(_first(r'propos du bien</h2>\s*<div[^>]*>(.*?)</div>', html))
        complement = clean_text(_first(r"Informations complémentaires :</div>\s*<div[^>]*>(.*?)</div>", html))
        address = clean_text(_first(r"""popupBlock \+= '</div><div class="inline-block">([^<]*)<""", html))
        address = address.strip(" ,")
        postal = re.search(r"\b(\d{5})\s+([^,]+)", address)
        marker = re.search(r"L\.marker\(\[\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*\]", html)
        cabinet = clean_text(_first(r'<span class="font-bold text-18">(.*?)</span>', html))
        documents = [{"titre": clean_text(label), "url": href} for href, label in re.findall(
            r'<a href="(https://avoventes\.fr/public/uploads/[^"]+\.pdf)"[^>]*>(.*?)</a>', html)]
        images = list(dict.fromkeys(re.findall(
            r'(https://avoventes\.fr/public/uploads/cabinet/\d+/images/resized_[^"\'\s)]+\.(?:jpe?g|png|webp))',
            html)))
        full_text = "\n\n".join(part for part in (description, complement) if part)
        tribunal = _tribunal(full_text)
        visits = _first(r"VISITES\s*:\s*(.*?)(?:% Estimez|$)", head_text)
        kind = type_from_text(title)
        if kind == "autre" and badges:
            kind = type_from_text(badges[0])
        surface = stats.get("m² superficie") or stats.get("m2 superficie") or parse_surface(title)
        city = postal.group(2).strip() if postal else None
        details = {
            "Statut": statut, "Mise à prix": mise, "Tribunal": tribunal, "Avocat": cabinet,
            "Occupation": occupation(full_text), "Visites": visits.strip() if visits else None,
            "Surenchère jusqu'au": parse_fr_date(_first(r"Surench[eè]re possible jusqu'au\s*:\s*([^<\n]+?\d{4})",
                                                           head_text)),
            "Surenchère déposée": "Oui" if re.search(r"Surench[eè]re d[ée]pos[ée]e", head_text, re.I) else None,
            "Faculté de baisse": _first(r"(avec faculté de baisse[^.]*?)(?:Frais|Consignation|$)", head_text),
            "Frais préalables": parse_amount(_first(r"Frais pr[ée]a?lables\s*:\s*([\d\s.,]+)\s*€", head_text)),
            "Consignation": parse_amount(_first(r"Consignation\s*:\s*([\d\s.,]+)\s*€", head_text)),
            "Charges annuelles": parse_amount(_first(r"Charges annuelles\s*:\s*([^<]+)", html)),
            "Adresse": address or None, "Catégorie site": badges[0] if badges else None,
            "Documents": documents, "Clé enchère": cle_enchere(tribunal, date_vente, city, mise),
        }
        yield make_record(
            id=page_url.rstrip("/").rsplit("/", 1)[-1], name=title, price=mise, prix_adjuge=adjuge,
            surface=None if kind == "terrain" else surface,
            land_surface=land_surface(kind, full_text, surface),
            rooms=stats.get("pièces") or stats.get("pieces"), bedrooms=stats.get("chambres"),
            zipcode=postal.group(1) if postal else None, city=city,
            lat=marker.group(1) if marker else None, lng=marker.group(2) if marker else None,
            type_hint=kind, url=page_url, body=full_text or None, seller_name=cabinet or None,
            reference_annonce=page_url.rstrip("/").rsplit("/", 1)[-1], mode_vente=self.MODE_VENTE,
            date_vente=date_vente, details=details, images=images,
            raw_payload=json.dumps({"entete": head_text[:4000]}, ensure_ascii=False),
        )


register(AvoventesConnector())
