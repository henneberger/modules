"""An experimental module and distribution layer for ordinary Python."""

from .catalog import Catalog, ManifestError

__version__ = "0.9.0"
__all__ = ["Catalog", "ManifestError", "__version__"]
