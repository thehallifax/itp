"""Safe opt-in bridge from canonical pipeline output to state history."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .engine import StateHistoryEngine, observation_from_payload
from .models import ObservationCompleteness, ObservationScope, PipelineRun
from .store import FileStateStore


def _utc(value=None):
    value = value or datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class PipelineStateCapture:
    """Capture a written canonical document with conservative authority."""

    def __init__(self, settings=None):
        self.settings = settings or {}
        self.enabled = bool(self.settings.get("enabled", False))
        self.output_path = Path(self.settings.get(
            "store_path", "/app/runtime/state-history"))
        self.removal_policy = self.settings.get(
            "removal_policy", "complete_only")
        if self.removal_policy not in {"complete_only", "disabled"}:
            raise ValueError("state_history.removal_policy must be complete_only or disabled")
        policy = self.settings.get("volatile_fields_policy", "default")
        if policy != "default":
            raise ValueError("state_history.volatile_fields_policy must be default")
        self.expected_sources = tuple(sorted(set(
            self.settings.get("expected_sources") or [])))
        self.engine = StateHistoryEngine(FileStateStore(self.output_path))

    def capture_generated(self, payload, *, started_at, completed_at=None,
                          canonical_output=""):
        if not self.enabled:
            return None
        observation = observation_from_payload(payload)
        completed = _utc(completed_at)
        started = _utc(started_at)
        raw_assets = payload.get("assets", [])

        def scope_sources(site_id, domain):
            values = {
                value.source for value in observation.entities
                if (value.site_id, value.domain) == (site_id, domain)
                and value.source}
            if domain == "infrastructure":
                for asset in raw_assets:
                    raw_site = asset.get("site")
                    if isinstance(raw_site, dict):
                        raw_site = raw_site.get("site_id") or raw_site.get("id")
                    raw_site = str(asset.get("site_id") or raw_site or "unassigned")
                    if raw_site != site_id:
                        continue
                    values.update(str(item) for item in asset.get("sources", [])
                                  if item)
                    value = asset.get("source") or asset.get("collector")
                    if value:
                        values.add(str(value))
            return tuple(sorted(values))

        observed_sources = tuple(sorted({
            source for site_id, domain in observation.scopes
            for source in scope_sources(site_id, domain)}))
        scopes = []
        for site_id, domain in observation.scopes:
            sources = scope_sources(site_id, domain)
            expected = self.expected_sources
            missing = tuple(sorted(set(expected) - set(sources)))
            # Absence of an explicit expected-source contract is never
            # interpreted as complete removal authority.
            completeness = (
                ObservationCompleteness.COMPLETE.value
                if expected and not missing
                else ObservationCompleteness.PARTIAL.value
                if sources else ObservationCompleteness.UNKNOWN.value)
            warnings = ()
            if not expected:
                warnings = ("expected source coverage is not configured",)
            elif missing:
                warnings = ("missing expected sources: " + ", ".join(missing),)
            scopes.append(ObservationScope(
                site_id=site_id, domain=domain, completeness=completeness,
                expected_sources=expected, observed_sources=sources,
                failed_sources=missing, warning_details=warnings))
        material = {
            "completed_at": completed,
            "canonical_output": canonical_output,
            "payload": payload,
            "sources": observed_sources,
        }
        run_id = "pipeline:" + hashlib.sha256(json.dumps(
            material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
        run = PipelineRun(
            run_id=run_id, started_at=started, completed_at=completed,
            status="success", scopes=tuple(sorted(
                scopes, key=lambda value: value.authority)),
            canonical_output=canonical_output)
        return self.engine.capture(run, observation,
                                   removal_policy=self.removal_policy)
