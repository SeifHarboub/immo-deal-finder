import importlib
import pkgutil

from .base import Connector, get_connector, register


def discover_connectors() -> None:
    """Importe automatiquement les modules connecteurs qui s'enregistrent."""
    ignored = {"base", "agency"}
    for module in pkgutil.iter_modules(__path__):
        if module.name not in ignored and not module.name.startswith("_"):
            importlib.import_module(f"{__name__}.{module.name}")

__all__ = ["Connector", "discover_connectors", "get_connector", "register"]
