"""Canonical site registry public API."""
from .aliases import normalize_alias
from .models import SiteDefinition, SiteResolution
from .registry import SiteRegistry
from .resolver import SiteResolver

__all__ = ["SiteDefinition", "SiteRegistry", "SiteResolution", "SiteResolver", "normalize_alias"]
