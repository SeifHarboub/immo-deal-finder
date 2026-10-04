from dataclasses import dataclass, field
from typing import Literal


@dataclass(slots=True)
class Criteria:
    categorie: Literal["vente", "location"] = "vente"
    types_bien: list[str] = field(default_factory=list)
    zones: list[str] = field(default_factory=list)
    prix_min: float | None = None
    prix_max: float | None = None
    surface_bati_min: float | None = None
    surface_terrain_min: float | None = None
    search_url: str | None = None
    max_pages: int | None = None


ANNONCES_STG_COLUMNS = [
    "source", "external_id", "categorie", "type_bien", "prix", "loyer",
    "surface_bati", "surface_terrain", "code_postal", "ville", "lat", "lng",
    "titre", "url", "collected_at",
    "description", "nb_pieces", "nb_chambres", "dpe", "ges",
    "seller_type", "seller_name", "image_count", "published_at",
]
