"""Typed canonical-site records."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SiteDefinition:
    id: str
    display_name: str
    aliases: tuple = field(default_factory=tuple)
    metadata: dict = field(default_factory=dict)

    @property
    def site_id(self):
        return self.id if self.id.startswith("site:") else f"site:{self.id}"


@dataclass(frozen=True)
class SiteResolution:
    site_id: str | None
    display_name: str
    source_value: str
    status: str
    matched_alias: str = ""
    explanation: str = ""

    def to_dict(self):
        return {"site_id": self.site_id, "display_name": self.display_name,
                "source_value": self.source_value, "status": self.status,
                "matched_alias": self.matched_alias,
                "explanation": self.explanation}
