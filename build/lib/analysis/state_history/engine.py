"""Canonical input adaptation and deterministic field-level comparison."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone

from .models import (
    CaptureResult,
    ChangeSet,
    EntityState,
    Observation,
    ObservationCompleteness,
    PipelineRun,
    StateChange,
    StateSnapshot,
)


DEFAULT_VOLATILE_FIELDS = frozenset({
    "collected_at", "collection_duration", "collection_duration_seconds",
    "generated_at", "last_observed", "last_seen_at", "observed_at",
    "source_last_seen_at", "timestamp", "uptime_seconds",
})
DEFAULT_UNORDERED_FIELDS = frozenset({
    "affected_assets", "affected_service_ids", "evidence", "ip_addresses",
    "mac_addresses", "network_ids", "sources", "storage_ids", "tags",
    "uplink_evidence",
})
STATUS_FIELDS = frozenset({
    "connection_state", "ha_status", "health", "lifecycle_state", "online",
    "operational_status", "power_state", "reachable", "state", "status",
})
SEVERITY = {
    "critical": "Critical", "failed": "Critical", "offline": "Critical",
    "down": "Critical", "unreachable": "Critical",
    "warning": "Medium", "degraded": "Medium", "stale": "Medium",
    "healthy": "Info", "online": "Info", "up": "Info",
}


def _iso(value, label):
    if not value:
        raise ValueError(f"{label} timestamp is required")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc


def _hash(prefix, value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True)
    return prefix + hashlib.sha256(encoded.encode()).hexdigest()[:24]


def _site(value):
    site = value.get("site")
    if isinstance(site, dict):
        return str(site.get("site_id") or site.get("id") or "unassigned")
    return str(value.get("site_id") or site or "unassigned")


def _entity(value, *, domain, entity_type, entity_id, observed_at,
            collected_at=None, source="", provider=""):
    if not isinstance(value, dict):
        raise ValueError("canonical entity must be a mapping")
    if not entity_id:
        raise ValueError(f"{domain} entity is missing a stable identity")
    return EntityState(
        site_id=_site(value), domain=domain, entity_type=str(entity_type),
        entity_id=str(entity_id), state=copy.deepcopy(value),
        observed_at=_iso(value.get("observed_at") or observed_at, "entity observation"),
        collected_at=(None if not (value.get("collected_at") or collected_at)
                      else _iso(value.get("collected_at") or collected_at,
                                "entity collection")),
        source=str(value.get("source") or value.get("collector") or source or ""),
        provider=str(value.get("provider") or provider or ""),
    )


def observation_from_payload(payload, observed_at=None):
    """Adapt canonical platform documents without reading vendor payloads."""
    if not isinstance(payload, dict):
        raise ValueError("state-history input must be a JSON mapping")
    timestamp = _iso(observed_at or payload.get("observed_at")
                     or payload.get("generated_at"), "observation")
    collected = payload.get("collected_at")
    if collected:
        collected = _iso(collected, "collection")
    source = str(payload.get("source") or "")
    provider = str(payload.get("provider") or "")
    entities = []
    scopes = set()

    if "entities" in payload:
        if not isinstance(payload["entities"], list):
            raise ValueError("entities must be a list")
        for raw in payload["entities"]:
            if not isinstance(raw, dict) or not isinstance(raw.get("state"), dict):
                raise ValueError("each entity requires a state mapping")
            entity = EntityState(
                site_id=str(raw.get("site_id") or "unassigned"),
                domain=str(raw.get("domain") or payload.get("domain") or ""),
                entity_type=str(raw.get("entity_type") or ""),
                entity_id=str(raw.get("entity_id") or ""),
                state=copy.deepcopy(raw["state"]),
                observed_at=_iso(raw.get("observed_at") or timestamp,
                                 "entity observation"),
                collected_at=(None if not (raw.get("collected_at") or collected)
                              else _iso(raw.get("collected_at") or collected,
                                        "entity collection")),
                source=str(raw.get("source") or source),
                provider=str(raw.get("provider") or provider),
            )
            entities.append(entity)
            scopes.add((entity.site_id, entity.domain))
        if payload.get("site_id") and payload.get("domain"):
            scopes.add((str(payload["site_id"]), str(payload["domain"])))
    elif "objects" in payload:
        values = payload["objects"]
        if not isinstance(values, list):
            raise ValueError("virtualisation objects must be a list")
        for value in values:
            entity = _entity(
                value, domain="virtualisation",
                entity_type=value.get("kind") or "object",
                entity_id=value.get("canonical_id"), observed_at=timestamp,
                collected_at=collected, source=source, provider=provider)
            entities.append(entity); scopes.add((entity.site_id, entity.domain))
        if not scopes:
            scopes.add((str(payload.get("site_id") or "unassigned"),
                        "virtualisation"))
    elif any(key in payload for key in ("issues", "risks", "recommendations")):
        for collection, entity_type in (
                ("issues", "issue"), ("risks", "risk"),
                ("recommendations", "recommendation")):
            values = payload.get(collection, [])
            if not isinstance(values, list):
                raise ValueError(f"{collection} must be a list")
            for value in values:
                entity = _entity(
                    value, domain="operations", entity_type=entity_type,
                    entity_id=value.get("id"), observed_at=timestamp,
                    collected_at=collected, source=source, provider=provider)
                entities.append(entity); scopes.add((entity.site_id, entity.domain))
        if not scopes:
            scopes.add((str(payload.get("site_id") or "unassigned"), "operations"))
    elif "assets" in payload:
        values = payload["assets"]
        if not isinstance(values, list):
            raise ValueError("infrastructure assets must be a list")
        for value in values:
            entity = _entity(
                value, domain="infrastructure", entity_type="asset",
                entity_id=(value.get("canonical_id") or value.get("asset_id")
                           or value.get("source_asset_id")),
                observed_at=timestamp, collected_at=collected, source=source,
                provider=provider)
            entities.append(entity); scopes.add((entity.site_id, entity.domain))
        for site in payload.get("sites", []):
            if isinstance(site, dict) and (site.get("site_id") or site.get("id")):
                scopes.add((str(site.get("site_id") or site["id"]),
                            "infrastructure"))
        if not scopes:
            scopes.add((str(payload.get("site_id") or "unassigned"),
                        "infrastructure"))
    else:
        raise ValueError(
            "input is not a canonical entities, infrastructure, operations, "
            "or virtualisation document")

    return Observation(
        observed_at=timestamp,
        collected_at=collected,
        source=source,
        provider=provider,
        entities=tuple(sorted(entities, key=lambda value: value.identity)),
        scopes=tuple(sorted(scopes)),
        schema_version=int(payload.get("schema_version", 1)),
    )


def pipeline_run_from_payload(payload):
    """Validate and normalize explicit pipeline-run completeness metadata."""
    run = PipelineRun.from_dict(payload)
    return replace(
        run,
        started_at=_iso(run.started_at, "pipeline start"),
        completed_at=_iso(run.completed_at, "pipeline completion"),
    )


class StateHistoryEngine:
    """Compare canonical snapshots and persist deterministic change sets."""

    def __init__(self, store, *, volatile_fields=None, unordered_fields=None):
        self.store = store
        self.volatile_fields = frozenset(
            volatile_fields if volatile_fields is not None
            else DEFAULT_VOLATILE_FIELDS)
        self.unordered_fields = frozenset(
            unordered_fields if unordered_fields is not None
            else DEFAULT_UNORDERED_FIELDS)

    def _normalise(self, value, path=()):
        if isinstance(value, dict):
            return {
                key: self._normalise(item, path + (key,))
                for key, item in sorted(value.items())
                if key not in self.volatile_fields
            }
        if isinstance(value, list):
            values = [self._normalise(item, path) for item in value]
            if path and path[-1] in self.unordered_fields:
                return sorted(values, key=lambda item: json.dumps(
                    item, sort_keys=True, separators=(",", ":")))
            return values
        return value

    def _snapshot(self, observation, site_id, domain, *, run_id="",
                  completeness=ObservationCompleteness.COMPLETE.value,
                  warning_details=(), carried_entities=()):
        observed = tuple(
            EntityState(**{**entity.to_dict(),
                "state": self._normalise(entity.state)})
            for entity in observation.entities
            if (entity.site_id, entity.domain) == (site_id, domain))
        values = {(entity.entity_type, entity.entity_id): entity
                  for entity in carried_entities}
        values.update({(entity.entity_type, entity.entity_id): entity
                       for entity in observed})
        entities = tuple(values[key] for key in sorted(values))
        material = {
            "schema_version": observation.schema_version,
            "site_id": site_id, "domain": domain,
            "observed_at": observation.observed_at,
            "collected_at": observation.collected_at,
            "entities": [entity.to_dict() for entity in entities],
        }
        if run_id:
            material.update({
                "run_id": run_id, "completeness": completeness,
                "warning_details": list(warning_details),
            })
        sources = sorted({entity.source for entity in entities if entity.source})
        providers = sorted({entity.provider for entity in entities if entity.provider})
        return StateSnapshot(
            snapshot_id=_hash("snapshot:", material),
            observed_at=observation.observed_at,
            collected_at=observation.collected_at,
            site_id=site_id, domain=domain, entities=entities,
            source=(sources[0] if len(sources) == 1 else
                    "multiple" if sources else observation.source),
            provider=(providers[0] if len(providers) == 1 else
                      "multiple" if providers else observation.provider),
            schema_version=observation.schema_version,
            run_id=run_id, completeness=completeness,
            warning_details=tuple(warning_details),
        )

    @staticmethod
    def _severity(value):
        if isinstance(value, bool):
            return "Info" if value else "Critical"
        return SEVERITY.get(str(value).casefold())

    def _change(self, previous, current, entity, change_type, path,
                previous_value, current_value):
        identity = {
            "previous_snapshot_id": previous.snapshot_id if previous else None,
            "current_snapshot_id": current.snapshot_id,
            "site_id": entity.site_id, "domain": entity.domain,
            "entity_type": entity.entity_type, "entity_id": entity.entity_id,
            "change_type": change_type, "field_path": path,
            "previous_value": previous_value, "current_value": current_value,
        }
        return StateChange(
            change_id=_hash("change:", identity),
            previous_snapshot_id=identity["previous_snapshot_id"],
            current_snapshot_id=current.snapshot_id,
            site_id=entity.site_id, domain=entity.domain,
            entity_type=entity.entity_type, entity_id=entity.entity_id,
            change_type=change_type, field_path=path,
            previous_value=previous_value, current_value=current_value,
            observed_at=current.observed_at, source=entity.source,
            provider=entity.provider,
            severity=(self._severity(current_value)
                      if change_type == "status_changed" else None),
        )

    def _fields(self, previous, current, entity, old, new, path=()):
        changes = []
        if isinstance(old, dict) and isinstance(new, dict):
            for key in sorted(set(old) | set(new)):
                nested_path = path + (key,)
                if key not in old:
                    change_type = (
                        "status_changed" if key in STATUS_FIELDS
                        else "field_changed")
                    changes.append(self._change(
                        previous, current, entity, change_type,
                        ".".join(nested_path), None, new[key]))
                elif key not in new:
                    change_type = (
                        "status_changed" if key in STATUS_FIELDS
                        else "field_changed")
                    changes.append(self._change(
                        previous, current, entity, change_type,
                        ".".join(nested_path), old[key], None))
                else:
                    changes.extend(self._fields(
                        previous, current, entity, old[key], new[key],
                        nested_path))
            return changes
        if old == new:
            return changes
        dotted = ".".join(path)
        change_type = (
            "status_changed" if path and path[-1] in STATUS_FIELDS
            else "field_changed")
        return [self._change(
            previous, current, entity, change_type, dotted, old, new)]

    def compare(self, previous, current, *, run_id="", completeness=None,
                removals_suppressed=0):
        old = {} if previous is None else {
            (entity.entity_type, entity.entity_id): entity
            for entity in previous.entities}
        new = {(entity.entity_type, entity.entity_id): entity
               for entity in current.entities}
        changes = []
        for identity in sorted(set(old) | set(new)):
            prior = old.get(identity); present = new.get(identity)
            if prior is None:
                changes.append(self._change(
                    previous, current, present, "entity_added", "",
                    None, present.state))
            elif present is None:
                changes.append(self._change(
                    previous, current, prior, "entity_removed", "",
                    prior.state, None))
            else:
                changes.extend(self._fields(
                    previous, current, present, prior.state, present.state))
        changes = tuple(sorted(changes, key=lambda value: (
            value.entity_type, value.entity_id, value.field_path,
            value.change_type, value.change_id)))
        material = {
            "previous_snapshot_id": previous.snapshot_id if previous else None,
            "current_snapshot_id": current.snapshot_id,
            "changes": [change.to_dict() for change in changes],
        }
        return ChangeSet(
            change_set_id=_hash("changeset:", material),
            previous_snapshot_id=material["previous_snapshot_id"],
            current_snapshot_id=current.snapshot_id,
            site_id=current.site_id, domain=current.domain,
            observed_at=current.observed_at, changes=changes,
            schema_version=current.schema_version,
            run_id=run_id,
            completeness=completeness or current.completeness,
            removals_suppressed=removals_suppressed,
        )

    def process(self, observation):
        snapshots = []; change_sets = []
        for site_id, domain in observation.scopes:
            current = self._snapshot(observation, site_id, domain)
            previous = self.store.latest(site_id, domain)
            changes = self.compare(previous, current)
            snapshots.append(current); change_sets.append(changes)
        self.store.commit_batch(tuple(zip(snapshots, change_sets)))
        return {
            "schema_version": 1,
            "observed_at": observation.observed_at,
            "snapshots": [value.to_dict() for value in snapshots],
            "change_sets": [value.to_dict() for value in change_sets],
            "change_count": sum(len(value.changes) for value in change_sets),
        }

    def process_payload(self, payload, observed_at=None):
        return self.process(observation_from_payload(payload, observed_at))

    def capture(self, run, observation, *, removal_policy="complete_only"):
        """Capture one successful run using explicit scope authority."""
        if removal_policy not in {"complete_only", "disabled"}:
            raise ValueError(f"unsupported removal policy: {removal_policy}")
        if not isinstance(run, PipelineRun):
            raise ValueError("capture requires PipelineRun metadata")
        existing = self.store.capture_result(run.run_id)
        if existing is not None:
            return existing
        if run.status != "success":
            material = {"run": run.to_dict(), "status": "skipped"}
            return CaptureResult(
                capture_id=_hash("capture:", material), run_id=run.run_id,
                status="skipped", pipeline_run=run.to_dict(),
                warning_details=tuple(sorted(set(
                    (*run.warning_details,
                     f"pipeline status {run.status} prevents state capture")))))

        metadata = {scope.authority: scope for scope in run.scopes}
        observed_authorities = set(observation.scopes)
        undeclared = observed_authorities - set(metadata)
        if undeclared:
            raise ValueError(
                "pipeline metadata is missing observed scope(s): " +
                ", ".join(f"{site}/{domain}" for site, domain in sorted(undeclared)))

        entries = []; scope_results = []; warnings = set(run.warning_details)
        for authority in sorted(metadata):
            site_id, domain = authority
            scope = metadata[authority]
            previous = self.store.latest(site_id, domain)
            observed_entities = tuple(
                entity for entity in observation.entities
                if (entity.site_id, entity.domain) == authority)
            authoritative = (
                removal_policy == "complete_only"
                and scope.completeness ==
                ObservationCompleteness.COMPLETE.value)
            prior_entities = () if previous is None else previous.entities
            observed_ids = {
                (entity.entity_type, entity.entity_id)
                for entity in observed_entities}
            missing = tuple(
                entity for entity in prior_entities
                if (entity.entity_type, entity.entity_id) not in observed_ids)
            suppressed = 0 if authoritative else len(missing)
            warnings.update(scope.warning_details)

            if not authoritative and not observed_entities:
                scope_results.append({
                    **scope.to_dict(), "captured": False,
                    "removal_authoritative": False,
                    "removals_suppressed": suppressed,
                    "previous_snapshot_id": (
                        previous.snapshot_id if previous else None),
                    "current_snapshot_id": None,
                    "change_set_id": None, "change_count": 0,
                })
                continue

            current = self._snapshot(
                observation, site_id, domain, run_id=run.run_id,
                completeness=scope.completeness,
                warning_details=scope.warning_details,
                carried_entities=missing if not authoritative else ())
            change_set = self.compare(
                previous, current, run_id=run.run_id,
                completeness=scope.completeness,
                removals_suppressed=suppressed)
            entries.append((current, change_set))
            scope_results.append({
                **scope.to_dict(), "captured": True,
                "removal_authoritative": authoritative,
                "removals_suppressed": suppressed,
                "previous_snapshot_id": (
                    previous.snapshot_id if previous else None),
                "current_snapshot_id": current.snapshot_id,
                "change_set_id": change_set.change_set_id,
                "change_count": len(change_set.changes),
            })

        degraded = any(
            scope.completeness != ObservationCompleteness.COMPLETE.value
            for scope in run.scopes)
        snapshots = tuple(snapshot.to_dict() for snapshot, _ in entries)
        change_sets = tuple(change_set.to_dict() for _, change_set in entries)
        material = {
            "run": run.to_dict(), "scope_results": scope_results,
            "snapshots": snapshots, "change_sets": change_sets,
        }
        result = CaptureResult(
            capture_id=_hash("capture:", material), run_id=run.run_id,
            status="degraded" if degraded else "captured",
            pipeline_run=run.to_dict(),
            scope_results=tuple(scope_results), snapshots=snapshots,
            change_sets=change_sets,
            warning_details=tuple(sorted(warnings)),
        )
        self.store.commit_batch(entries, result)
        return result

    def capture_payload(self, run_payload, state_payload, *,
                        removal_policy="complete_only"):
        run = pipeline_run_from_payload(run_payload)
        observation = observation_from_payload(
            state_payload, observed_at=run.completed_at)
        # Explicit expected scopes remain visible even when a failed/skipped
        # source produced no entities in the canonical document.
        observation = replace(
            observation,
            scopes=tuple(sorted(
                set(observation.scopes)
                | {scope.authority for scope in run.scopes})))
        return self.capture(
            run, observation, removal_policy=removal_policy)
