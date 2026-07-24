"""Replaceable state-history storage boundary and filesystem implementation."""
from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from pathlib import Path

from collectors.writer import atomic_write
from .models import ChangeSet, StateSnapshot


class StateStore(ABC):
    """Storage interface independent of the comparison engine."""

    @abstractmethod
    def latest(self, site_id, domain):
        """Return the latest snapshot for a canonical scope, if one exists."""

    @abstractmethod
    def write_snapshot(self, snapshot):
        """Persist a snapshot without changing the latest pointer."""

    @abstractmethod
    def write_change_set(self, change_set):
        """Persist a change set."""

    @abstractmethod
    def set_latest(self, snapshot):
        """Atomically point a canonical scope at a persisted snapshot."""


class FileStateStore(StateStore):
    """Atomic JSON store suitable for local runtime and deterministic tests."""

    def __init__(self, root):
        self.root = Path(root)
        self.snapshots = self.root / "snapshots"
        self.change_sets = self.root / "changes"
        self.latest_dir = self.root / "latest"

    @staticmethod
    def _scope_id(site_id, domain):
        material = json.dumps([site_id, domain], separators=(",", ":"))
        return hashlib.sha256(material.encode()).hexdigest()[:24]

    def snapshot_path(self, snapshot_id):
        return self.snapshots / f"{snapshot_id}.json"

    def change_set_path(self, change_set_id):
        return self.change_sets / f"{change_set_id}.json"

    def latest_path(self, site_id, domain):
        return self.latest_dir / f"{self._scope_id(site_id, domain)}.json"

    def latest(self, site_id, domain):
        pointer = self.latest_path(site_id, domain)
        if not pointer.exists():
            return None
        try:
            reference = json.loads(pointer.read_text())
            if (reference.get("site_id"), reference.get("domain")) != (site_id, domain):
                raise ValueError("latest state pointer scope does not match")
            payload = json.loads(
                self.snapshot_path(reference["snapshot_id"]).read_text())
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError("invalid filesystem state store") from exc
        return StateSnapshot.from_dict(payload)

    def write_snapshot(self, snapshot):
        atomic_write(self.snapshot_path(snapshot.snapshot_id),
                     json.dumps(snapshot.to_dict(), indent=2, sort_keys=True) + "\n")

    def write_change_set(self, change_set):
        atomic_write(self.change_set_path(change_set.change_set_id),
                     json.dumps(change_set.to_dict(), indent=2, sort_keys=True) + "\n")

    def set_latest(self, snapshot):
        pointer = {"schema_version": 1, "snapshot_id": snapshot.snapshot_id,
                   "site_id": snapshot.site_id, "domain": snapshot.domain}
        atomic_write(self.latest_path(snapshot.site_id, snapshot.domain),
                     json.dumps(pointer, indent=2, sort_keys=True) + "\n")
