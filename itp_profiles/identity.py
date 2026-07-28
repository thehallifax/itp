"""Deterministic deployment, customer, site, and device identity contracts."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


MACHINE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")


class IdentityError(ValueError):
    pass


def validate_id(label, value, *, prefix=None):
    value = str(value or "").strip()
    if not value or not MACHINE_ID.fullmatch(value):
        raise IdentityError(
            f"{label} must be a stable machine-safe identifier")
    if prefix and not value.startswith(prefix):
        raise IdentityError(f"{label} must start with {prefix}")
    return value


@dataclass(frozen=True)
class CanonicalIdentity:
    deployment_id: str
    customer_id: str
    site_id: str | None = None
    customer_name: str = ""
    site_name: str = ""

    def tags(self):
        values = {
            "deployment_id": self.deployment_id,
            "customer_id": self.customer_id,
            "customer": self.customer_id,
        }
        if self.site_id:
            values.update({
                "site_id": self.site_id,
                "site": self.site_id,
                "site_name": self.site_name or self.site_id,
            })
        if self.customer_name:
            values["customer_name"] = self.customer_name
        return values


class IdentityResolver:
    """Resolve legacy site aliases at one ingestion boundary."""

    def __init__(self, deployment_id, customer_id, sites=(),
                 customer_name=""):
        self.deployment_id = validate_id("deployment_id", deployment_id)
        self.customer_id = validate_id("customer_id", customer_id)
        self.customer_name = str(customer_name or "")
        self.sites = {site["site_id"]: site for site in sites}
        self.aliases = {}
        for site in sites:
            for alias in (
                    site["site_id"], site["site_id"][5:],
                    site["display_name"], *site["aliases"]):
                key = self.normalize_alias(alias)
                if key and key in self.aliases \
                        and self.aliases[key] != site["site_id"]:
                    raise IdentityError(
                        f"site alias {alias!r} maps to multiple site IDs")
                if key:
                    self.aliases[key] = site["site_id"]

    @staticmethod
    def normalize_alias(value):
        return " ".join(str(value or "").strip().casefold().split())

    @classmethod
    def from_sites_file(cls, deployment_id, customer_id, path,
                        customer_name=""):
        payload = yaml.safe_load(Path(path).read_text()) or {}
        sites = []
        for value in payload.get("sites", []):
            raw_id = str(value.get("id") or "").strip()
            site_id = raw_id if raw_id.startswith("site:") \
                else f"site:{raw_id}"
            validate_id("site_id", site_id, prefix="site:")
            sites.append({
                "site_id": site_id,
                "display_name": str(
                    value.get("display_name") or value.get("name") or site_id),
                "aliases": tuple(str(item) for item in
                                 value.get("aliases", [])),
            })
        return cls(
            deployment_id, customer_id, sites,
            customer_name=customer_name)

    def resolve_site(self, value):
        key = self.normalize_alias(value)
        site_id = self.aliases.get(key)
        if not site_id:
            raise IdentityError(
                f"site identity {value!r} is not present in the profile registry")
        site = self.sites[site_id]
        return CanonicalIdentity(
            self.deployment_id, self.customer_id, site_id,
            self.customer_name, site["display_name"])

    def deployment(self):
        return CanonicalIdentity(
            self.deployment_id, self.customer_id,
            customer_name=self.customer_name)
