"""Vench : ventes judiciaires nationales (recoupe largement Licitor).

Seules les ventes à venir et quelques pages récentes de résultats sont lues :
le résultat détaillé est réservé aux abonnés, il n'est donc pas contourné. La
« Clé enchère » permet le dédoublonnage avec Licitor et Avoventes.
"""

from collections.abc import Iterator
import json
import re
from urllib.parse import urljoin

from immo.connectors.base import register
from immo.connectors.licitor import (
    EnchereConnector, cle_enchere, clean_text, land_surface, make_record, occupation, parse_amount,
    parse_fr_date, parse_rooms, parse_surface, round_robin, type_from_text,
)


BASE = "https://www.vench.fr"
_LINK = re.compile(r'href="(?:\./|/)?(vente-\d+-[^"]+\.html)"')


def _first(pattern: str, text: str, flags=re.S | re.I) -> str | None:
    match = re.search(pattern, text or "", flags)
    return match.group(1).strip() if match else None


class VenchConnector(EnchereConnector):
    PARSER_VERSION = 1

    def __init__(self) -> None:
        super().__init__("vench", BASE + "/prochaines-ventes-aux-encheres.html")

    def _extract(self, html: str) -> tuple[list[str], int | None]:
        urls = list(dict.fromkeys(urljoin(BASE + "/", link) for link in _LINK.findall(html)))
        pages = [int(value) for value in re.findall(r"\?p=(\d+)", html)]
        return urls, max(pages) if pages else None

    def _upcoming(self) -> Iterator[str]:
        cap = int(self._env("VENCH_UPCOMING_MAX_PAGES", 60))
        page, total = 1, 1
        while page <= min(total, cap):
            listing = self._listing_page(f"{BASE}/prochaines-ventes-aux-encheres.html?p={page}", self._extract)
            if not listing or not listing[0]:
                return
            yield from listing[0]
            total = listing[1] or page
            page += 1

    def _candidate_urls(self) -> Iterator[str]:
        url_for = lambda page: f"{BASE}/resultats-des-ventes-encheres-immobilieres.html?p={page}"  # noqa: E731
        yield from round_robin(
            self._upcoming(),
            self._fresh_pages(url_for, self._extract, int(self._env("VENCH_FRESH_MAX_PAGES", 3))),
            self._backfill_pages("resultats", url_for, self._extract,
                                 int(self._env("VENCH_HISTORY_PAGES_PER_RUN", 0))),
        )

    def parse_page(self, html: str, page_url: str) -> Iterator[dict]:
        number = _first(r"/vente-(\d+)-", page_url)
        title = clean_text(_first(r"<h1[^>]*>(.*?)</h1>", html))
        text = clean_text(re.sub(r"<(script|style|svg)\b.*?</\1>", " ", html, flags=re.S))
        mise = parse_amount(_first(r"Mise à prix\s*:\s*([\d\s.,]+)\s*€", text))
        if not number or not title or mise is None:
            return
        parts = [part.strip() for part in title.split("•")]
        tribunal = _first(r"Ventes aux enchères publiques\s*-\s*(Tribunal[^\n]+)", text)
        address = _first(r"\nAdresse\n(.*?)\nVoir la carte", text) or ""
        address_lines = [line.strip() for line in address.splitlines() if line.strip()]
        postal = next((line for line in address_lines if re.fullmatch(r"\d{5}", line)), None)
        city = address_lines[-1] if address_lines and address_lines[-1] != postal else (parts[-1] if parts else None)
        date_vente = parse_fr_date(_first(r"DATE DE L'AUDIENCE\s*\n\s*([^\n]+)", text))
        description = _first(r"\nDescription\n(.*?)\n(?:Occupation|Caractéristiques|Documents)\n", text) or ""
        occupation_text = _first(r"\nOccupation\n([^\n]+)", text)
        lawyer = " ".join((_first(r"Avocat poursuivant\s*\n(.*?)\n\s*Demande d'informations", text) or "").split())
        coords = re.search(r"var lat\s*=\s*(-?\d+\.\d+);\s*var lon\s*=\s*(-?\d+\.\d+);", html)
        adjuge = parse_amount(_first(r"Adjug[ée]\w*\s*(?:à|:)?\s*([\d\s.,]+)\s*€", text))
        statut = "à venir"
        if adjuge:
            statut = "adjugé"
        elif re.search(r"carence d'ench[eè]res?|ench[eè]res? d[ée]sertes?", text, re.I) and "VENTE TERMIN" in text:
            statut = "carence"
        elif re.search(r"VENTE TERMIN[ÉE]E", text):
            statut = "non communiqué"
        elif re.search(r"vente (?:retirée|annulée|radiée|suspendue)", text, re.I):
            statut = "retiré"
        kind = type_from_text(parts[0] if parts else title)
        surface = parse_surface(title) or parse_surface(description)
        body = "\n\n".join(part for part in (description, f"Occupation : {occupation_text}" if occupation_text else "")
                           if part)
        details = {
            "Statut": statut, "Mise à prix": mise, "Tribunal": tribunal, "Avocat": lawyer or None,
            "Occupation": occupation(occupation_text or description),
            "Visites": _first(r"DATE\(S\) DE VISITE\s*\n(.*?)\n\s*(?:Enregistrer|J\s*-)", text),
            "Faculté de baisse": _first(r"faculté de baisse de prix\s*\n\s*(Oui|Non)", text),
            "Consignation": _first(r"Consignation\s*:\s*\n?\s*([^\n]+)", text),
            "Surenchère": "Oui" if re.search(r"\bSurench[eè]re\b", text) and statut == "à venir" else None,
            "Référence greffe": _first(r"\(RG\s*:\s*([^)]+)\)", text),
            "Adresse": ", ".join(address_lines) or None,
            "Clé enchère": cle_enchere(tribunal, date_vente, city, mise),
        }
        yield make_record(
            id=number, name=title, price=mise, prix_adjuge=adjuge,
            surface=None if kind == "terrain" else surface, land_surface=land_surface(kind, description, surface),
            rooms=parse_rooms(title + "\n" + description), zipcode=postal, city=city,
            lat=coords.group(1) if coords else None, lng=coords.group(2) if coords else None,
            type_hint=kind, url=page_url, body=body or None, seller_name=lawyer or None,
            published_at=parse_fr_date(_first(r"Publication\s*:\s*([\d/]+)", text)),
            reference_annonce=number, mode_vente=self.MODE_VENTE, date_vente=date_vente, details=details,
            raw_payload=json.dumps({"titre": title, "texte": text[:4000]}, ensure_ascii=False),
        )


register(VenchConnector())
