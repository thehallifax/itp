"""Typed canonical-site records."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SiteDefinition:
    id: str
    display_name: str
    aliases: tuple = field(default_factory=tuple)
    metadata: dict = field(default_factory=dict)
    type: str = "other"
    parent_id: str | None = None
    rollup_group: str | None = None
    enabled: bool = True
    display_order: int | None = None
    description: str = ""

    @property
    def site_id(self):
        return self.id if self.id.startswith("site:") else f"site:{self.id}"

    @property
    def canonical_parent_id(self):
        if not self.parent_id:
            return None
        return self.parent_id if self.parent_id.startswith("site:") else f"site:{self.parent_id}"


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
