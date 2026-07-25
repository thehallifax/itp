"""Replaceable state-history storage boundary and filesystem implementation."""
from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from pathlib import Path

from collectors.writer import atomic_remove, atomic_write
from .models import CaptureResult, ChangeSet, StateSnapshot


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

    def capture_result(self, run_id):
        """Return an existing idempotent capture result, when supported."""
        return None

    def commit_batch(self, entries, capture_result=None):
        """Commit immutable documents before exposing their latest pointers."""
        for snapshot, change_set in entries:
            self.write_snapshot(snapshot)
            self.write_change_set(change_set)
        for snapshot, _ in entries:
            self.set_latest(snapshot)


class FileStateStore(StateStore):
    """Atomic JSON store suitable for local runtime and deterministic tests."""

    def __init__(self, root):
        self.root = Path(root)
        self.snapshots = self.root / "snapshots"
        self.change_sets = self.root / "changes"
        self.latest_dir = self.root / "latest"
        self.runs = self.root / "runs"

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

    def capture_result_path(self, run_id):
        return self.runs / (
            hashlib.sha256(str(run_id).encode()).hexdigest()[:24] + ".json")

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
        snapshot = StateSnapshot.from_dict(payload)
        if (snapshot.snapshot_id != reference["snapshot_id"]
                or snapshot.site_id != site_id or snapshot.domain != domain):
            raise ValueError("persisted latest snapshot identity does not match")
        return snapshot

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

    def capture_result(self, run_id):
        path = self.capture_result_path(run_id)
        if not path.exists():
            return None
        try:
            result = CaptureResult.from_dict(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("invalid persisted capture result") from exc
        if result.run_id != run_id:
            raise ValueError("persisted capture result run identity does not match")
        return result

    def write_capture_result(self, result):
        atomic_write(self.capture_result_path(result.run_id),
                     json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n")

    def commit_batch(self, entries, capture_result=None):
        """Expose no new latest pointer until every immutable write succeeds.

        Immutable snapshot/change files may remain orphaned after interruption;
        they are never visible through ``latest`` and stable IDs make reruns
        safe. Pointer writes are rolled back if a later pointer or run-result
        write fails.
        """
        entries = tuple(entries)
        authorities = [(snapshot.site_id, snapshot.domain)
                       for snapshot, _ in entries]
        if len(authorities) != len(set(authorities)):
            raise ValueError("capture transaction contains duplicate scope authority")
        for snapshot, change_set in entries:
            if (change_set.current_snapshot_id != snapshot.snapshot_id
                    or (change_set.site_id, change_set.domain) !=
                    (snapshot.site_id, snapshot.domain)):
                raise ValueError("capture transaction snapshot/change-set mismatch")
        for snapshot, change_set in entries:
            self.write_snapshot(snapshot)
            self.write_change_set(change_set)

        backups = {}
        try:
            for snapshot, _ in entries:
                path = self.latest_path(snapshot.site_id, snapshot.domain)
                backups[path] = path.read_text() if path.exists() else None
                self.set_latest(snapshot)
            if capture_result is not None:
                self.write_capture_result(capture_result)
        except Exception:
            for path, content in backups.items():
                if content is None:
                    atomic_remove(path)
                else:
                    atomic_write(path, content)
            raise
