from abc import ABC, abstractmethod
from collections.abc import Iterator
import os
import random
import time

from immo.schema import Criteria


class Connector(ABC):
    source: str

    @abstractmethod
    def fetch(self, c: Criteria) -> Iterator[dict]:
        """Retourne les enregistrements bruts dans le schéma natif de la source."""

    def _pause(self) -> None:
        suffix = self.source.upper().replace("-", "_")
        minimum = float(os.getenv(
            f"SCRAPE_MIN_DELAY_{suffix}", os.getenv("SCRAPE_MIN_DELAY", "6")
        ))
        maximum = float(os.getenv(
            f"SCRAPE_MAX_DELAY_{suffix}", os.getenv("SCRAPE_MAX_DELAY", "14")
        ))
        time.sleep(random.uniform(minimum, max(minimum, maximum)))


_CONNECTORS: dict[str, Connector] = {}


def register(connector: Connector) -> Connector:
    _CONNECTORS[connector.source] = connector
    return connector


def get_connector(name: str) -> Connector:
    if name not in _CONNECTORS:
        raise ValueError(f"Connecteur inconnu : {name}")
    return _CONNECTORS[name]
