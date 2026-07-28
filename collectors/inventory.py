"""Vendor-neutral inventory engine with legacy JSON compatibility."""
from __future__ import annotations
import fnmatch
import hashlib
import ipaddress
import json
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .writer import atomic_write
from .file_lock import exclusive_file_lock


SCHEMA_VERSION = 2
LIFECYCLE_STATES = {"discovered", "active", "offline", "stale", "missing", "retired"}
SECRET_FIELDS = {"api_token", "token", "password", "secret", "authorization",
                 "authorization_header", "community", "community_string", "communities"}
TRACKED_CHANGE_FIELDS = ("hostname", "display_name", "management_ip", "mac_address",
    "serial_number", "source_asset_id", "vendor", "model", "platform", "device_type",
    "device_role", "customer", "site", "location", "firmware_version", "claimed",
    "managed", "source_priority", "source_record_id")
PROTECTED_IDENTITY_FIELDS = {"serial_number", "mac_address", "source_asset_id"}
SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}


class InventoryError(ValueError):
    """Actionable inventory error which never includes record contents."""


def utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise InventoryError("inventory contains an invalid ISO-8601 timestamp") from exc
    return result.astimezone(timezone.utc)


def _clean(value):
    return str(value).strip() if value not in (None, "") else None


def _normal_mac(value):
    if not value:
        return None
    compact = "".join(character for character in str(value).lower() if character in "0123456789abcdef")
    return compact if len(compact) == 12 else None


def _normal_serial(value):
    return "".join(str(value).upper().split()) if value not in (None, "") else None


def _normal_ip(value):
    if value in (None, ""): return None
    try: return str(ipaddress.ip_address(str(value).strip()))
    except ValueError: return str(value).strip()


def _safe_change_value(value):
    value = _without_secrets(value)
    if isinstance(value, str): return value[:512]
    if isinstance(value, (bool, int, float)) or value is None: return value
    return str(value)[:512]


def _without_secrets(value):
    if isinstance(value, dict):
        return {key: _without_secrets(item) for key, item in value.items()
                if not _secret_field_name(key)}
    if isinstance(value, list):
        return [_without_secrets(item) for item in value]
    return value


def _secret_field_name(key):
    name = str(key).lower()
    if name == "community_index": return False
    return (name in SECRET_FIELDS or name.endswith(("_token", "_password", "_secret")) or
            "authorization" in name or name.endswith(("_community", "_community_string")))


def stable_asset_id(source, record):
    """Return a stable, source-scoped ID using the strongest available key."""
    source = str(source or "unknown").lower()
    serial = _normal_serial(record.get("serial_number") or record.get("serial"))
    source_id = _clean(record.get("source_asset_id") or record.get("external_device_id"))
    chassis_mac = _normal_mac(record.get("chassis_mac"))
    management_mac = _normal_mac(record.get("management_mac") or record.get("mac_address") or record.get("mac"))
    if serial:
        kind, value = "serial", serial
    elif source_id:
        kind, value = "source-id", source_id
    elif chassis_mac:
        kind, value = "chassis-mac", chassis_mac
    elif management_mac:
        kind, value = "management-mac", management_mac
    else:
        fallback = [record.get(key) for key in
                    ("id", "management_ip", "ip", "sys_object_id", "hostname", "model")]
        value = json.dumps([source, *fallback], separators=(",", ":"), ensure_ascii=True)
        kind = "fallback"
    digest = hashlib.sha256(f"{source}|{kind}|{value}".encode()).hexdigest()[:24]
    return f"asset:{source}:{digest}"


@dataclass
class InventoryAsset:
    asset_id: str
    source: str
    collector: str
    lifecycle_state: str
    first_seen_at: str | None
    last_seen_at: str | None
    last_changed_at: str | None
    source_asset_id: str | None = None
    vendor: str | None = None
    platform: str | None = None
    device_type: str | None = None
    device_role: str | None = None
    hostname: str | None = None
    display_name: str | None = None
    model: str | None = None
    serial_number: str | None = None
    mac_address: str | None = None
    management_ip: str | None = None
    firmware_version: str | None = None
    deployment_id: str | None = None
    customer_id: str | None = None
    site_id: str | None = None
    customer: str | None = None
    site: str | None = None
    location: str | None = None
    retired_at: str | None = None
    source_record_id: str | None = None
    source_priority: int | None = None
    source_last_seen_at: str | None = None
    online: bool | None = None
    claimed: bool | None = None
    managed: bool | None = None
    lifecycle_reason: str | None = None
    lifecycle_source_run_id: str | None = None
    last_observed_source_run_id: str | None = None
    reconciliation_status: str = "unmatched"
    reconciliation_evidence: list | None = None
    extensions: dict | None = None

    def to_dict(self):
        return {key: value for key, value in asdict(self).items() if value is not None}


class InventoryEngine:
    """Deterministic JSON inventory, reconciliation, and lifecycle interface."""

    def __init__(self, persistence_path, *, legacy_path=None, stale_after_seconds=86400,
                 missing_after_seconds=604800, lifecycle_history_max_events=10000,
                 lifecycle_history_retention_days=365, change_detection=None):
        self.root = Path(persistence_path)
        self.assets_path = self.root / "assets.json"
        self.reconciliation_path = self.root / "reconciliation.json"
        self.source_runs_path = self.root / "source_runs.json"
        self.history_path = self.root / "lifecycle_history.json"
        self.change_history_path = self.root / "change_history.json"
        self.legacy_path = Path(legacy_path) if legacy_path else self.root / "devices.json"
        self.stale_after_seconds = int(stale_after_seconds)
        self.missing_after_seconds = int(missing_after_seconds)
        self.history_max_events = int(lifecycle_history_max_events)
        self.history_retention_days = int(lifecycle_history_retention_days)
        changes = change_detection or {}
        self.change_detection_enabled = changes.get("enabled", True)
        self.change_history_max_events = int(changes.get("history_max_events", 20000))
        self.change_history_retention_days = int(changes.get("history_retention_days", 365))
        self.duplicate_suppression_seconds = int(changes.get("duplicate_suppression_seconds", 3600))
        self.change_ignored_fields = set(changes.get("ignored_fields", []))
        self.change_minimum_severity = changes.get("minimum_severity", "info")
        self.field_minimum_severity = dict(changes.get("field_minimum_severity", {}))
        self.hostname_exclusions = tuple(changes.get("hostname_pattern_exclusions", []))
        self.device_type_exclusions = set(changes.get("device_type_exclusions", []))
        self.enrichment_only_fields = set(changes.get("enrichment_only_fields",
            ["serial_number", "mac_address", "source_asset_id"]))
        if self.stale_after_seconds < 0 or self.missing_after_seconds < self.stale_after_seconds:
            raise InventoryError("inventory thresholds require 0 <= stale_after_seconds <= missing_after_seconds")
        if self.history_max_events < 1 or self.history_retention_days < 1:
            raise InventoryError("inventory lifecycle history retention values must be positive")
        if (self.change_history_max_events < 1 or self.change_history_retention_days < 1 or
                self.duplicate_suppression_seconds < 0):
            raise InventoryError("inventory change history retention values must be positive")
        if self.change_minimum_severity not in SEVERITY_RANK or any(
                value not in SEVERITY_RANK for value in self.field_minimum_severity.values()):
            raise InventoryError("inventory change severity must be info, low, medium, or high")

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / ".inventory.lock", "a+") as lock:
            with exclusive_file_lock(lock):
                yield

    @staticmethod
    def _read_json(path, missing):
        try:
            text = path.read_text()
        except FileNotFoundError:
            return missing
        except OSError as exc:
            raise InventoryError(f"cannot read inventory file {path.name}: {exc}") from exc
        if not text.strip():
            return missing
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise InventoryError(f"inventory file {path.name} contains malformed JSON") from exc

    def _legacy_assets(self):
        value = self._read_json(self.legacy_path, {"devices": []})
        if isinstance(value, list):
            records, defaults = value, {}
        elif isinstance(value, dict):
            records, defaults = value.get("devices", []), value
        else:
            raise InventoryError(f"inventory file {self.legacy_path.name} must contain an object or list")
        if not isinstance(records, list):
            raise InventoryError(f"inventory file {self.legacy_path.name} has an invalid devices list")
        assets = []
        for record in records:
            if not isinstance(record, dict):
                continue
            source = record.get("source") or ("snmp" if record.get("sys_object_id") else "legacy")
            assets.append(self._adapt(source, record, defaults.get("customer"), defaults.get("site"),
                                      record.get("last_seen") or record.get("last_seen_at")))
        return sorted(assets, key=lambda item: item["asset_id"])

    def load(self):
        value = self._read_json(self.assets_path, None)
        if value is None:
            return {"schema_version": SCHEMA_VERSION, "assets": self._legacy_assets()}
        if isinstance(value, list):
            value = {"schema_version": SCHEMA_VERSION, "assets": value}
        if not isinstance(value, dict) or not isinstance(value.get("assets"), list):
            raise InventoryError("inventory file assets.json must contain an assets list")
        return {"schema_version": SCHEMA_VERSION,
                "assets": sorted((_without_secrets(item) for item in value["assets"] if isinstance(item, dict)),
                                 key=lambda item: item.get("asset_id", ""))}

    def save(self, inventory):
        assets = sorted((_without_secrets(item) for item in inventory.get("assets", [])),
                        key=lambda item: item.get("asset_id", ""))
        value = {"schema_version": SCHEMA_VERSION, "assets": assets}
        content = json.dumps(value, indent=2, sort_keys=True) + "\n"
        try:
            current = self.assets_path.read_text()
        except OSError:
            current = None
        if current != content:
            atomic_write(self.assets_path, content)
        return value

    @staticmethod
    def _event_id(asset_id, previous_state, new_state, reason, occurred_at, source_run_id=None):
        value = "|".join(str(item or "") for item in
                         (asset_id, previous_state, new_state, reason, occurred_at, source_run_id))
        return "event:" + hashlib.sha256(value.encode()).hexdigest()[:24]

    @staticmethod
    def _run_id(source, started_at):
        return "run:" + hashlib.sha256(f"{source}|{started_at}".encode()).hexdigest()[:24]

    @staticmethod
    def _write_json(path, value):
        content = json.dumps(_without_secrets(value), indent=2, sort_keys=True) + "\n"
        try: current = path.read_text()
        except OSError: current = None
        if current != content: atomic_write(path, content)
        return value

    def load_source_runs(self):
        value = self._read_json(self.source_runs_path, {"schema_version": 1, "sources": {}})
        if not isinstance(value, dict) or not isinstance(value.get("sources"), dict):
            raise InventoryError("inventory file source_runs.json must contain a sources object")
        return _without_secrets(value)

    def begin_source_run(self, source, collector=None, started_at=None):
        started_at = started_at or utcnow(); source = str(source).lower()
        run = {"run_id": self._run_id(source, started_at), "source": source,
               "collector": collector or source, "started_at": started_at,
               "completed_at": None, "success": None, "records_returned": 0,
               "partial": False, "error_category": None}
        with self.locked():
            value = self.load_source_runs(); state = value["sources"].setdefault(source,
                {"source": source, "collector": collector or source, "consecutive_successes": 0,
                 "consecutive_failures": 0, "runs": []})
            state["collector"] = collector or source
            state["runs"] = [item for item in state.get("runs", []) if item.get("run_id") != run["run_id"]]
            state["runs"].append(run); state["runs"] = state["runs"][-100:]
            state["last_run"] = run
            self._write_json(self.source_runs_path, value)
        return run["run_id"]

    def complete_source_run(self, source, run_id, *, success, records_returned=0,
                            completed_at=None, error_category=None, partial=False):
        completed_at = completed_at or utcnow(); source = str(source).lower()
        with self.locked():
            value = self.load_source_runs(); state = value["sources"].setdefault(source,
                {"source": source, "collector": source, "consecutive_successes": 0,
                 "consecutive_failures": 0, "runs": []})
            run = next((item for item in state.get("runs", []) if item.get("run_id") == run_id), None)
            if run is None:
                run = {"run_id": run_id, "source": source, "collector": state.get("collector", source),
                       "started_at": completed_at}
                state.setdefault("runs", []).append(run)
            run.update({"completed_at": completed_at, "success": bool(success),
                        "records_returned": int(records_returned), "partial": bool(partial),
                        "error_category": _clean(error_category) if not success else None})
            state["last_run"] = dict(run)
            if success:
                state["last_successful_run"] = dict(run)
                if not partial: state["last_complete_successful_run"] = dict(run)
                state["consecutive_successes"] = int(state.get("consecutive_successes", 0)) + 1
                state["consecutive_failures"] = 0
            else:
                state["last_failed_run"] = dict(run)
                state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
                state["consecutive_successes"] = 0
            state["runs"] = state.get("runs", [])[-100:]
            self._write_json(self.source_runs_path, value)
            return dict(run)

    def load_history(self):
        value = self._read_json(self.history_path, {"schema_version": 1, "events": []})
        if not isinstance(value, dict) or not isinstance(value.get("events"), list):
            raise InventoryError("inventory file lifecycle_history.json must contain an events list")
        return {"schema_version": 1, "events": [_without_secrets(item) for item in value["events"]
                                                 if isinstance(item, dict)]}

    def _append_transition(self, item, previous_state, new_state, reason, occurred_at, *,
                           actor="system", source_run_id=None, metadata=None):
        if previous_state == new_state:
            return None
        event = {"event_id": self._event_id(item["asset_id"], previous_state, new_state, reason,
                                             occurred_at, source_run_id),
                 "asset_id": item["asset_id"], "source": item.get("source", "unknown"),
                 "previous_state": previous_state, "new_state": new_state, "reason": reason,
                 "occurred_at": occurred_at, "observed_at": item.get("last_seen_at"),
                 "source_run_id": source_run_id, "actor": actor,
                 "metadata": _without_secrets(metadata or {})}
        history = self.load_history(); events = history["events"]
        if any(existing.get("event_id") == event["event_id"] for existing in events):
            return None
        events.append(event)
        cutoff = _parse_time(occurred_at) - timedelta(days=self.history_retention_days)
        events = [existing for existing in events
                  if _parse_time(existing.get("occurred_at")) is not None and
                  _parse_time(existing.get("occurred_at")) >= cutoff]
        events.sort(key=lambda existing: (existing.get("occurred_at", ""), existing.get("event_id", "")))
        history["events"] = events[-self.history_max_events:]
        self._write_json(self.history_path, history)
        return event

    def history(self, asset_id=None, *, state=None, source=None, limit=50):
        events = self.load_history()["events"]
        if asset_id: events = [item for item in events if item.get("asset_id") == asset_id]
        if state: events = [item for item in events if item.get("new_state") == state]
        if source: events = [item for item in events if item.get("source") == source]
        return list(reversed(events))[:max(1, int(limit))]

    def load_changes(self):
        value = self._read_json(self.change_history_path, {"schema_version": 1, "events": []})
        if not isinstance(value, dict) or not isinstance(value.get("events"), list):
            raise InventoryError("inventory file change_history.json must contain an events list")
        return {"schema_version": 1, "events": [_without_secrets(item) for item in value["events"]
                                                 if isinstance(item, dict)]}

    @staticmethod
    def _comparison_value(field, value):
        if value in (None, ""): return None
        if field == "mac_address": return _normal_mac(value)
        if field == "management_ip": return _normal_ip(value)
        if field == "hostname": return str(value).strip().casefold()
        if field in ("claimed", "managed"): return bool(value)
        if isinstance(value, str): return value.strip()
        return value

    @staticmethod
    def _change_classification(field, previous, new):
        if field in PROTECTED_IDENTITY_FIELDS:
            if previous is None and new is not None:
                return "identity_enriched", "info", "protected_identity_added"
            return "identity_conflict", "high", "protected_identity_changed"
        if field == "firmware_version": return "firmware_changed", "info", "firmware_observation_changed"
        if field == "model": return "classification_changed", "high", "hardware_model_changed"
        if field in ("vendor", "platform", "device_type", "device_role"):
            return "classification_changed", "medium", "asset_classification_changed"
        if field in ("customer", "site", "location"):
            return "location_changed", "medium", "asset_location_changed"
        if field in ("claimed", "managed", "source_priority", "source_record_id"):
            severity = "medium" if new in (None, False) else "low"
            return "ownership_changed", severity, "asset_ownership_changed"
        change_type = "value_added" if previous is None else ("value_removed" if new is None else "value_changed")
        severity = "info" if previous is None else (
            "low" if field in ("hostname", "display_name", "management_ip") else "info")
        return change_type, severity, "inventory_value_changed"

    def _new_change_event(self, item, field, previous, new, detected_at, source_run_id):
        change_type, severity, reason = self._change_classification(field, previous, new)
        required = self.field_minimum_severity.get(field, self.change_minimum_severity)
        if SEVERITY_RANK[severity] < SEVERITY_RANK[required]: return None
        signature = json.dumps([item["asset_id"], field, previous, new, change_type, detected_at,
                                source_run_id], separators=(",", ":"), sort_keys=True)
        return {"event_id": "change:" + hashlib.sha256(signature.encode()).hexdigest()[:24],
            "asset_id": item["asset_id"], "source": item.get("source", "unknown"),
            "collector": item.get("collector", item.get("source", "unknown")), "field": field,
            "previous_value": _safe_change_value(previous), "new_value": _safe_change_value(new),
            "change_type": change_type, "severity": severity, "reason": reason,
            "detected_at": detected_at, "observed_at": detected_at,
            "source_run_id": source_run_id, "actor": "collector",
            "metadata": {"authoritative_observation": True,
                         "protected_identity": field in PROTECTED_IDENTITY_FIELDS}}

    def _append_changes(self, additions, now):
        if not additions: return []
        history = self.load_changes(); events = history["events"]; accepted = []
        for event in additions:
            duplicate = False
            for existing in reversed(events + accepted):
                if (existing.get("asset_id"), existing.get("field"), existing.get("previous_value"),
                    existing.get("new_value"), existing.get("change_type")) != (
                    event.get("asset_id"), event.get("field"), event.get("previous_value"),
                    event.get("new_value"), event.get("change_type")):
                    continue
                existing_time = _parse_time(existing.get("detected_at"))
                if existing_time and (_parse_time(event["detected_at"]) - existing_time).total_seconds() <= \
                        self.duplicate_suppression_seconds:
                    duplicate = True
                break
            if not duplicate and not any(item.get("event_id") == event["event_id"] for item in events):
                accepted.append(event)
        if not accepted: return []
        events.extend(accepted)
        cutoff = _parse_time(now) - timedelta(days=self.change_history_retention_days)
        events = [item for item in events if _parse_time(item.get("detected_at")) is not None and
                  _parse_time(item.get("detected_at")) >= cutoff]
        events.sort(key=lambda item: (item.get("detected_at", ""), item.get("event_id", "")))
        history["events"] = events[-self.change_history_max_events:]
        self._write_json(self.change_history_path, history)
        return accepted

    def _detect_changes(self, previous, item, authoritative_fields, partial, detected_at, source_run_id):
        if not self.change_detection_enabled: return []
        hostname = item.get("hostname") or previous.get("hostname") or ""
        if any(fnmatch.fnmatch(hostname.casefold(), pattern.casefold()) for pattern in self.hostname_exclusions):
            return []
        if (item.get("device_type") or previous.get("device_type")) in self.device_type_exclusions:
            return []
        events = []
        for field in TRACKED_CHANGE_FIELDS:
            if field in self.change_ignored_fields: continue
            old = previous.get(field); present = field in item
            authoritative = field in authoritative_fields
            if not present and (partial or not authoritative or field in self.enrichment_only_fields):
                if field in previous: item[field] = old
                continue
            new = item.get(field)
            if self._comparison_value(field, old) == self._comparison_value(field, new):
                if old is not None: item[field] = old
                continue
            event = self._new_change_event(item, field, old, new, detected_at, source_run_id)
            if event: events.append(event)
            if field in PROTECTED_IDENTITY_FIELDS and old is not None:
                item[field] = old
        return events

    def changes(self, asset_id=None, *, source=None, field=None, severity=None, since=None, limit=50):
        events = self.load_changes()["events"]
        if asset_id: events = [item for item in events if item.get("asset_id") == asset_id]
        if source: events = [item for item in events if item.get("source") == source]
        if field: events = [item for item in events if item.get("field") == field]
        if severity: events = [item for item in events if item.get("severity") == severity]
        if since:
            cutoff = _parse_time(since)
            events = [item for item in events if _parse_time(item.get("detected_at")) >= cutoff]
        return list(reversed(events))[:max(1, int(limit))]

    def changes_summary(self, since=None):
        events = self.changes(since=since, limit=self.change_history_max_events)
        def counts(key): return dict(sorted(Counter(item.get(key) or "unknown" for item in events).items()))
        assets = Counter(item["asset_id"] for item in events)
        return {"total_changes": len(events), "by_severity": counts("severity"),
            "by_field": counts("field"), "by_source": counts("source"),
            "assets_with_most_changes": [{"asset_id": key, "changes": value}
                for key, value in assets.most_common(10)],
            "identity_conflicts": sum(item.get("change_type") == "identity_conflict" for item in events),
            "firmware_changes": sum(item.get("field") == "firmware_version" for item in events),
            "site_or_location_changes": sum(item.get("field") in ("site", "location") for item in events),
            "ownership_or_management_changes": sum(item.get("field") in
                ("claimed", "managed", "source_priority", "source_record_id") for item in events)}

    @staticmethod
    def _adapt(source, record, customer=None, site=None, now=None):
        record = _without_secrets(record)
        source = str(source or record.get("source") or "unknown").lower()
        source_id = (_clean(record.get("source_asset_id")) or _clean(record.get("external_device_id"))
                     or _clean(record.get("id")))
        if source == "snmp" and not source_id and record.get("ip") and record.get("sys_object_id"):
            source_id = f"{record['ip']}|{record['sys_object_id']}"
        online = record.get("online")
        operational = str(record.get("operational_status") or record.get("status") or "").lower()
        if online is None and operational:
            if operational in ("online", "connected", "active"): online = True
            elif operational in ("offline", "disconnected", "unreachable"): online = False
        first = record.get("first_seen_at") or record.get("first_seen") or now
        last = record.get("last_seen_at") or record.get("last_seen") or now
        lifecycle = record.get("lifecycle_state")
        if lifecycle not in LIFECYCLE_STATES:
            lifecycle = "offline" if online is False else "discovered"
        known = {"asset_id", "source", "collector", "lifecycle_state", "first_seen_at", "first_seen",
                 "last_seen_at", "last_seen", "last_changed_at", "source_asset_id", "external_device_id",
                 "vendor", "platform", "device_type", "device_role", "hostname", "display_name", "name",
                 "model", "serial_number", "serial", "mac_address", "mac", "management_ip", "ip",
                 "firmware_version", "firmware", "deployment_id",
                 "customer_id", "site_id", "customer", "site",
                 "location", "retired_at",
                 "source_record_id", "source_priority", "source_last_seen_at", "online", "claimed",
                 "managed", "reconciliation_status", "reconciliation_evidence", "extensions",
                 "operational_status", "status", "community_index", "lifecycle_reason",
                 "lifecycle_source_run_id", "last_observed_source_run_id", "_authoritative_fields"}
        extensions = dict(record.get("extensions") or {})
        extensions.update({key: value for key, value in record.items() if key not in known})
        asset = InventoryAsset(
            asset_id=record.get("asset_id") or stable_asset_id(source, record), source=source,
            collector=str(record.get("collector") or source), source_asset_id=source_id,
            vendor=_clean(record.get("vendor")), platform=_clean(record.get("platform")),
            device_type=_clean(record.get("device_type") or record.get("platform")),
            device_role=_clean(record.get("device_role")), hostname=_clean(record.get("hostname")),
            display_name=_clean(record.get("display_name") or record.get("name")),
            model=_clean(record.get("model")), serial_number=_clean(record.get("serial_number") or record.get("serial")),
            mac_address=_normal_mac(record.get("mac_address") or record.get("mac")),
            management_ip=_normal_ip(record.get("management_ip") or record.get("ip")),
            firmware_version=_clean(record.get("firmware_version") or record.get("firmware")),
            deployment_id=_clean(record.get("deployment_id")),
            customer_id=_clean(record.get("customer_id") or
                               record.get("customer") or customer),
            site_id=_clean(record.get("site_id")),
            customer=_clean(record.get("customer") or customer), site=_clean(record.get("site") or site),
            location=_clean(record.get("location")), lifecycle_state=lifecycle,
            first_seen_at=first, last_seen_at=last,
            last_changed_at=record.get("last_changed_at") or first,
            retired_at=record.get("retired_at"), source_record_id=_clean(record.get("source_record_id") or source_id),
            source_priority=record.get("source_priority"), source_last_seen_at=record.get("source_last_seen_at") or last,
            online=online, claimed=record.get("claimed"), managed=record.get("managed"),
            lifecycle_reason=record.get("lifecycle_reason"),
            lifecycle_source_run_id=record.get("lifecycle_source_run_id"),
            last_observed_source_run_id=record.get("last_observed_source_run_id"),
            reconciliation_status=record.get("reconciliation_status", "unmatched"),
            reconciliation_evidence=record.get("reconciliation_evidence") or [],
            extensions=extensions or None,
        )
        return asset.to_dict()

    def ingest(self, source, records, *, customer=None, site=None, now=None, source_run_id=None,
               authoritative_fields=None, partial=False):
        now = now or utcnow()
        with self.locked():
            inventory = self.load()
            current = {item.get("asset_id"): item for item in inventory["assets"] if item.get("asset_id")}
            change_events = []
            for record in records:
                if not isinstance(record, dict):
                    continue
                item = self._adapt(source, record, customer, site, now)
                previous = current.get(item["asset_id"])
                if previous is None and item.get("source_asset_id"):
                    previous = next((candidate for candidate in current.values()
                        if candidate.get("source") == item["source"] and
                        candidate.get("source_asset_id") == item["source_asset_id"]), None)
                    if previous:
                        item["asset_id"] = previous["asset_id"]
                if previous:
                    previous_state = previous.get("lifecycle_state", "discovered")
                    item["first_seen_at"] = previous.get("first_seen_at") or item.get("first_seen_at")
                    if previous_state == "retired":
                        item["lifecycle_state"] = "retired"
                        item["retired_at"] = previous.get("retired_at")
                    else:
                        item["lifecycle_state"] = "offline" if item.get("online") is False else "active"
                    ignored = {"last_seen_at", "source_last_seen_at", "last_changed_at",
                               "reconciliation_status", "reconciliation_evidence"}
                    changed = any(previous.get(key) != value for key, value in item.items() if key not in ignored)
                    item["last_changed_at"] = now if changed else previous.get("last_changed_at", now)
                    new_state = item["lifecycle_state"]
                    if previous_state in ("stale", "missing") and new_state == "active": reason = "rediscovered"
                    elif new_state == "offline": reason = "source_reported_offline"
                    elif previous_state == "offline" and new_state == "active": reason = "source_reported_online"
                    else: reason = "repeated_observation"
                    self._append_transition(item, previous_state, new_state, reason, now,
                                            actor="collector", source_run_id=source_run_id)
                    declared = set(record.get("_authoritative_fields") or authoritative_fields or ())
                    change_events.extend(self._detect_changes(previous, item, declared, partial,
                                                               now, source_run_id))
                else:
                    self._append_transition(item, None, item["lifecycle_state"], "first_observation", now,
                                            actor="collector", source_run_id=source_run_id)
                item["last_seen_at"] = now
                item["source_last_seen_at"] = now
                if source_run_id: item["last_observed_source_run_id"] = source_run_id
                current[item["asset_id"]] = item
            inventory["assets"] = list(current.values())
            self.save(inventory)
            self.reconcile(inventory=inventory, save_assets=True)
            self._append_changes(change_events, now)
            return self.load()

    @staticmethod
    def _pair_status(left, right):
        if left.get("source") == right.get("source"):
            return None
        serial_a, serial_b = _normal_serial(left.get("serial_number")), _normal_serial(right.get("serial_number"))
        mac_a, mac_b = _normal_mac(left.get("mac_address")), _normal_mac(right.get("mac_address"))
        host_a, host_b = _clean(left.get("hostname")), _clean(right.get("hostname"))
        source_id_a, source_id_b = _clean(left.get("source_asset_id")), _clean(right.get("source_asset_id"))
        same_host = host_a and host_b and host_a.casefold() == host_b.casefold()
        if serial_a and serial_a == serial_b and mac_a and mac_b and mac_a != mac_b:
            return "conflicting", ["serial_number agrees", "mac_address conflicts"]
        if mac_a and mac_a == mac_b and serial_a and serial_b and serial_a != serial_b:
            return "conflicting", ["mac_address agrees", "serial_number conflicts"]
        if same_host and ((serial_a and serial_b and serial_a != serial_b) or (mac_a and mac_b and mac_a != mac_b)):
            return "conflicting", ["hostname agrees but strong identifiers conflict"]
        if serial_a and serial_a == serial_b and mac_a and mac_a == mac_b:
            return "exact_match", ["serial_number", "mac_address"]
        if serial_a and serial_a == serial_b:
            evidence = ["serial_number"]
            if left.get("model") and left.get("model") == right.get("model"): evidence.append("model")
            return "strong_match", evidence
        if mac_a and mac_a == mac_b:
            return "strong_match", ["mac_address"]
        if (source_id_a and source_id_a == source_id_b and left.get("vendor") and
                left.get("vendor") == right.get("vendor")):
            return "strong_match", ["shared_vendor_device_id", "vendor"]
        if same_host:
            return "ambiguous", ["hostname_only"]
        return None

    def reconcile(self, *, inventory=None, save_assets=True):
        inventory = inventory or self.load()
        assets = inventory["assets"]
        entries = []
        evidence_by_asset = {item["asset_id"]: [] for item in assets}
        rank = {"unmatched": 0, "source_only": 1, "ambiguous": 2, "strong_match": 3,
                "exact_match": 4, "conflicting": 5}
        status_by_asset = {item["asset_id"]: ("source_only" if any(
            item.get(key) for key in ("serial_number", "mac_address", "source_asset_id")) else "unmatched")
            for item in assets}
        for index, left in enumerate(assets):
            for right in assets[index + 1:]:
                result = self._pair_status(left, right)
                if not result:
                    continue
                status, evidence = result
                entry = {"asset_ids": sorted([left["asset_id"], right["asset_id"]]),
                         "status": status, "evidence": evidence}
                entries.append(entry)
                for item in (left, right):
                    if rank[status] >= rank[status_by_asset[item["asset_id"]]]:
                        status_by_asset[item["asset_id"]] = status
                    evidence_by_asset[item["asset_id"]].append(entry)
        for item in assets:
            item["reconciliation_status"] = status_by_asset[item["asset_id"]]
            item["reconciliation_evidence"] = sorted(evidence_by_asset[item["asset_id"]],
                                                       key=lambda entry: entry["asset_ids"])
        entries.sort(key=lambda entry: (entry["asset_ids"], entry["status"]))
        result = {"schema_version": 1, "reconciliations": entries}
        content = json.dumps(result, indent=2, sort_keys=True) + "\n"
        try: current = self.reconciliation_path.read_text()
        except OSError: current = None
        if current != content: atomic_write(self.reconciliation_path, content)
        if save_assets: self.save(inventory)
        return result

    def list_assets(self):
        return self.load()["assets"]

    def get_asset(self, asset_id):
        return next((item for item in self.list_assets() if item.get("asset_id") == asset_id), None)

    def mark_seen(self, asset_id, now=None, online=None):
        now = now or utcnow()
        with self.locked():
            inventory = self.load()
            item = next((asset for asset in inventory["assets"] if asset.get("asset_id") == asset_id), None)
            if item is None: raise InventoryError(f"unknown asset_id: {asset_id}")
            item["last_seen_at"] = now; item["source_last_seen_at"] = now
            if online is not None: item["online"] = bool(online)
            if item.get("lifecycle_state") != "retired":
                old = item.get("lifecycle_state", "discovered")
                new = "offline" if item.get("online") is False else "active"
                item["lifecycle_state"] = new
                reason = "source_reported_offline" if new == "offline" else (
                    "rediscovered" if old in ("stale", "missing") else "source_reported_online")
                self._append_transition(item, old, new, reason, now, actor="collector")
            self.save(inventory)
            return item

    def retire(self, asset_id, reason, now=None, actor="operator"):
        if not _clean(reason): raise InventoryError("retirement reason is required")
        now = now or utcnow()
        with self.locked():
            inventory = self.load()
            item = next((asset for asset in inventory["assets"] if asset.get("asset_id") == asset_id), None)
            if item is None: raise InventoryError(f"unknown asset_id: {asset_id}")
            old = item.get("lifecycle_state", "discovered")
            if old == "retired": return item
            item.update({"lifecycle_state": "retired", "retired_at": now,
                         "last_changed_at": now, "lifecycle_reason": "manually_retired"})
            self._append_transition(item, old, "retired", "manually_retired", now,
                                    actor=actor, metadata={"reason": reason})
            self.save(inventory)
            return item

    def _state_from_source_evidence(self, item, now_value):
        runs = self.load_source_runs().get("sources", {}).get(item.get("source"), {})
        successful = runs.get("last_complete_successful_run")
        last_seen = _parse_time(item.get("last_seen_at"))
        completed = _parse_time(successful.get("completed_at")) if successful else None
        if (last_seen is None or completed is None or successful.get("partial") is True or
                completed <= last_seen or
                successful.get("run_id") == item.get("last_observed_source_run_id")):
            return None, None
        age = (_parse_time(now_value) - last_seen).total_seconds()
        evidence_age = (completed - last_seen).total_seconds()
        if age >= self.missing_after_seconds and evidence_age >= self.missing_after_seconds:
            return "missing", successful
        if age >= self.stale_after_seconds and evidence_age >= self.stale_after_seconds:
            return "stale", successful
        return None, successful

    def restore(self, asset_id, reason, now=None, actor="operator"):
        if not _clean(reason): raise InventoryError("restoration reason is required")
        now = now or utcnow()
        with self.locked():
            inventory = self.load()
            item = next((asset for asset in inventory["assets"] if asset.get("asset_id") == asset_id), None)
            if item is None: raise InventoryError(f"unknown asset_id: {asset_id}")
            if item.get("lifecycle_state") != "retired": return item
            aged, run = self._state_from_source_evidence(item, now)
            new = aged or ("offline" if item.get("online") is False else "active")
            item.pop("retired_at", None)
            item.update({"lifecycle_state": new, "last_changed_at": now,
                         "lifecycle_reason": "manually_restored"})
            self._append_transition(item, "retired", new, "manually_restored", now,
                                    actor=actor, source_run_id=run.get("run_id") if run else None,
                                    metadata={"reason": reason})
            self.save(inventory)
            return item

    def set_lifecycle(self, asset_id, state, now=None):
        """Compatibility method for explicit state updates."""
        if state == "retired": return self.retire(asset_id, "explicit lifecycle update", now)
        if state not in LIFECYCLE_STATES:
            raise InventoryError(f"invalid lifecycle state: {state}")
        now = now or utcnow()
        with self.locked():
            inventory = self.load()
            item = next((asset for asset in inventory["assets"] if asset.get("asset_id") == asset_id), None)
            if item is None: raise InventoryError(f"unknown asset_id: {asset_id}")
            old = item.get("lifecycle_state", "discovered")
            item.update({"lifecycle_state": state, "last_changed_at": now,
                         "lifecycle_reason": "explicit_lifecycle_update"})
            item.pop("retired_at", None)
            self._append_transition(item, old, state, "explicit_lifecycle_update", now, actor="operator")
            self.save(inventory)
            return item

    def update_lifecycle(self, now=None):
        now_value = now or utcnow(); transitions = []
        with self.locked():
            inventory = self.load()
            for item in inventory["assets"]:
                old = item.get("lifecycle_state", "discovered")
                if old == "retired":
                    continue
                new, run = self._state_from_source_evidence(item, now_value)
                if new is None:
                    continue
                if new != old:
                    reason = f"{new}_threshold_exceeded"
                    item.update({"lifecycle_state": new, "last_changed_at": now_value,
                                 "lifecycle_reason": reason,
                                 "lifecycle_source_run_id": run.get("run_id")})
                    event = self._append_transition(item, old, new, reason, now_value,
                        actor="system", source_run_id=run.get("run_id"),
                        metadata={"threshold_seconds": self.missing_after_seconds if new == "missing"
                                  else self.stale_after_seconds})
                    if event: transitions.append(event)
            result = self.save(inventory)
            states = Counter(item.get("lifecycle_state", "unknown") for item in result["assets"])
            result["lifecycle_summary"] = {"assets_evaluated": len(result["assets"]),
                "transitions": len(transitions), "stale": states.get("stale", 0),
                "missing": states.get("missing", 0)}
            return result

    def summary(self, newest_limit=5):
        assets = self.list_assets()
        def counts(key):
            return dict(sorted(Counter(item.get(key) or "unknown" for item in assets).items()))
        newest = sorted(assets, key=lambda item: item.get("first_seen_at") or "", reverse=True)[:newest_limit]
        states = counts("lifecycle_state")
        reconciliations = counts("reconciliation_status")
        source_states = self.load_source_runs().get("sources", {})
        healthy = sorted(name for name, state in source_states.items()
                         if state.get("last_run", {}).get("success") is True and
                         state.get("last_run", {}).get("partial") is not True)
        failing = sorted(name for name, state in source_states.items()
                         if state.get("last_run", {}).get("success") is False)
        successes = [state.get("last_complete_successful_run", {}).get("completed_at")
                     for state in source_states.values()
                     if state.get("last_complete_successful_run", {}).get("completed_at")]
        observed_sources = {name for name, state in source_states.items()
                            if state.get("last_complete_successful_run")}
        return {"total_assets": len(assets), "by_collector": counts("collector"),
                "by_vendor": counts("vendor"), "by_device_type": counts("device_type"),
                "by_site": counts("site"), "by_lifecycle_state": states,
                "by_reconciliation_status": reconciliations,
                "newest_assets": [{key: item.get(key) for key in ("asset_id", "display_name", "hostname", "first_seen_at")
                                   if item.get(key) is not None} for item in newest],
                "stale_assets": states.get("stale", 0), "missing_assets": states.get("missing", 0),
                "assets_without_successful_source_observation": sum(
                    1 for item in assets if item.get("source") not in observed_sources),
                "sources_healthy": healthy, "sources_failing": failing,
                "oldest_successful_source_run": min(successes) if successes else None,
                "recent_lifecycle_transitions": self.history(limit=5)}


class InventoryManager:
    """Compatibility façade for devices.json plus the generic InventoryEngine."""

    def __init__(self, path, settings=None):
        self.path = Path(path)
        settings = settings or {}
        persistence = settings.get("persistence_path") or self.path.parent
        self.enabled = settings.get("enabled", True)
        self.preserve_legacy_outputs = settings.get("preserve_legacy_outputs", True)
        self.engine = InventoryEngine(persistence, legacy_path=self.path,
            stale_after_seconds=settings.get("stale_after_seconds", 86400),
            missing_after_seconds=settings.get("missing_after_seconds", 604800),
            lifecycle_history_max_events=settings.get("lifecycle_history_max_events", 10000),
            lifecycle_history_retention_days=settings.get("lifecycle_history_retention_days", 365),
            change_detection=settings.get("change_detection"))

    def read(self):
        try:
            value = json.loads(self.path.read_text())
            return value if isinstance(value, dict) else {"devices": []}
        except (OSError, json.JSONDecodeError):
            return {"devices": []}

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(f"{self.path}.lock", "a+") as lock:
            with exclusive_file_lock(lock):
                yield

    def write(self, inventory):
        atomic_write(self.path, json.dumps(inventory, indent=2) + "\n")
        return inventory

    def merge(self, config, discoveries, previous=None, now=None):
        from .snmp.discovery import merge_inventory
        return merge_inventory(config, discoveries, previous if previous is not None else self.read(), now)

    def update(self, config, discoveries, now=None, source_run_id=None, partial=False):
        with self.locked():
            result = self.write(self.merge(config, discoveries, now=now))
        if self.enabled:
            records = [item for item in result.get("devices", [])
                       if item.get("source") in (None, "snmp") and item.get("status") == "active"]
            authoritative = {
                "hostname", "management_ip", "vendor", "platform",
                "device_type", "device_role", "deployment_id",
                "customer_id", "site_id", "location"}
            self.engine.ingest("snmp", records, customer=result.get("customer"),
                               site=result.get("site"), now=result.get("generated_at"),
                               source_run_id=source_run_id, authoritative_fields=authoritative,
                               partial=partial)
        return result

    def update_source(self, records, source, customer, site, now, retention_days=7, source_run_id=None,
                      partial=False):
        """Replace one source's observations without disturbing other sources."""
        with self.locked():
            current = self.read()
            prior = {d.get("id"): d for d in current.get("devices", [])
                     if d.get("source") == source and d.get("id")}
            incoming_ids = set()
            merged = [d for d in current.get("devices", []) if d.get("source") != source]
            for record in records:
                item = dict(record)
                item.pop("_authoritative_fields", None)
                identity = item["id"]
                incoming_ids.add(identity)
                item["first_seen"] = prior.get(identity, {}).get("first_seen", now)
                item["last_seen"] = now
                item["status"] = "active"
                merged.append(item)
            cutoff = datetime.fromisoformat(now.replace("Z", "+00:00")) - timedelta(days=retention_days)
            for identity, old in prior.items():
                if identity in incoming_ids:
                    continue
                try:
                    last = datetime.fromisoformat(old["last_seen"].replace("Z", "+00:00"))
                except (KeyError, ValueError):
                    continue
                if last >= cutoff:
                    stale = dict(old)
                    stale["status"] = "stale"
                    merged.append(stale)
            current.update({"schema_version": current.get("schema_version", 1),
                            "customer": current.get("customer", customer),
                            "site": current.get("site", site), "devices": merged})
            result = self.write(current)
        if self.enabled:
            self.engine.ingest(source, records, customer=customer, site=site, now=now,
                               source_run_id=source_run_id, partial=partial)
        return result
