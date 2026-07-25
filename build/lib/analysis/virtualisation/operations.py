"""Formal promotion of canonical virtualisation evidence into Operations."""
from __future__ import annotations

import fnmatch
import hashlib
from datetime import datetime, timezone

from analysis.operations.models import OperationalItem, priority


def _service_ids(rule_id, obj, evidence):
    """Map only directly evidenced impact; object relationships are not outages."""
    if rule_id.startswith("virtualisation.collection_") or rule_id in {
            "virtualisation.manager_unreachable", "virtualisation.evidence_stale"}:
        return ("virtualisation_management_plane",)
    if rule_id == "virtualisation.cluster_degraded":
        values = ["hypervisor_cluster"]
        if evidence.get("remaining_capacity_insufficient") or evidence.get("quorum_compromised"):
            values.append("compute_capacity")
        if evidence.get("workload_impact_confirmed"):
            values.extend(("virtual_machine_hosting", "workload_availability"))
        return tuple(values)
    if rule_id in {"virtualisation.host_disconnected",
                   "virtualisation.host_not_responding"}:
        values = ["hypervisor_cluster"]
        if not obj.get("cluster_id"):
            values.extend(("compute_capacity", "virtual_machine_hosting"))
        if evidence.get("workload_impact_confirmed"):
            values.append("workload_availability")
        return tuple(values)
    if rule_id in {"virtualisation.host_cpu_high",
                   "virtualisation.host_memory_high"}:
        return ("compute_capacity",)
    if rule_id.startswith("virtualisation.storage_capacity_"):
        return ("shared_storage",)
    if rule_id == "virtualisation.storage_inaccessible":
        values = ["shared_storage"]
        if evidence.get("workload_impact_confirmed"):
            values.extend(("virtual_machine_hosting", "workload_availability"))
        return tuple(values)
    if rule_id in {"virtualisation.snapshot_stale",
                   "virtualisation.snapshot_count_excessive",
                   "virtualisation.unexpected_maintenance"}:
        return ("virtual_machine_hosting",)
    if rule_id == "virtualisation.ha_degraded":
        return ("hypervisor_cluster",)
    if rule_id in {"virtualisation.replication_unhealthy",
                   "virtualisation.expected_workload_stopped",
                   "virtualisation.workload_state_unknown",
                   "virtualisation.guest_agent_unhealthy"}:
        return ("workload_availability",)
    return ()

MAPPING = {
    "virtualisation.manager_unreachable": ("risk", "Unknown", "Management endpoint unreachable"),
    "virtualisation.collection_authentication": ("issue", "High", "Virtualisation authentication failed"),
    "virtualisation.collection_permission": ("risk", "Unknown", "Virtualisation permissions prevent evaluation"),
    "virtualisation.collection_partial": ("risk", "Unknown", "Virtualisation collection is partial"),
    "virtualisation.cluster_degraded": ("issue", "High", "Hypervisor cluster degraded"),
    "virtualisation.host_disconnected": ("issue", "High", "Hypervisor host disconnected"),
    "virtualisation.host_not_responding": ("issue", "High", "Hypervisor host not responding"),
    "virtualisation.unexpected_maintenance": ("risk", "Medium", "Unexpected hypervisor maintenance mode"),
    "virtualisation.host_cpu_high": ("risk", "Medium", "Hypervisor CPU capacity pressure"),
    "virtualisation.host_memory_high": ("risk", "Medium", "Hypervisor memory capacity pressure"),
    "virtualisation.storage_inaccessible": ("issue", "Critical", "Virtualisation storage inaccessible"),
    "virtualisation.storage_capacity_warning": ("risk", "Medium", "Virtualisation storage capacity warning"),
    "virtualisation.storage_capacity_critical": ("issue", "Critical", "Virtualisation storage capacity critical"),
    "virtualisation.workload_state_unknown": ("risk", "Unknown", "Workload state unknown"),
    "virtualisation.guest_agent_unhealthy": ("risk", "Low", "Guest integration agent unhealthy"),
    "virtualisation.snapshot_stale": ("risk", "Medium", "Stale snapshot or checkpoint"),
    "virtualisation.snapshot_count_excessive": ("risk", "Medium", "Excessive snapshots or checkpoints"),
    "virtualisation.ha_degraded": ("risk", "Medium", "Virtualisation HA is disabled or degraded"),
    "virtualisation.replication_unhealthy": ("issue", "Critical", "Required workload replication is unhealthy"),
    "virtualisation.expected_workload_stopped": ("issue", "High", "Expected workload is stopped"),
    "virtualisation.evidence_stale": ("risk", "Unknown", "Virtualisation evidence is stale"),
}


def _time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def validate_expectations(values):
    """Validate deterministic first-match workload expectation rules."""
    seen = set()
    result = []
    for index, value in enumerate(values or []):
        if not isinstance(value, dict) or not isinstance(value.get("match"), dict):
            raise ValueError(f"workload expectation {index + 1} must contain a match mapping")
        match = value["match"]
        if set(match) - {"name", "tag"} or len(match) != 1:
            raise ValueError(f"workload expectation {index + 1} must match exactly one name or tag")
        identity = tuple(sorted((key, str(item)) for key, item in match.items()))
        if identity in seen:
            raise ValueError(f"conflicting workload expectation: {dict(identity)}")
        seen.add(identity)
        expected = str(value.get("expected_state", "running")).casefold()
        if expected not in {"running", "stopped", "any"}:
            raise ValueError(f"workload expectation {index + 1} has invalid expected_state")
        result.append({**value, "expected_state": expected, "priority": int(value.get("priority", index))})
    priorities = [value["priority"] for value in result]
    if len(priorities) != len(set(priorities)):
        raise ValueError("workload expectation priorities must be unique")
    return sorted(result, key=lambda value: value["priority"])


class VirtualisationOperationsAdapter:
    """Promote provider-neutral findings without inferring workload outages."""

    def __init__(self, expectations=None, stale_seconds=900):
        self.expectations = validate_expectations(expectations)
        self.stale_seconds = max(60, int(stale_seconds))

    def _expectation(self, obj):
        matches = []
        for rule in self.expectations:
            match = rule["match"]
            if "name" in match and fnmatch.fnmatchcase(obj.get("name", ""), str(match["name"])):
                matches.append(rule)
            elif "tag" in match and any(fnmatch.fnmatchcase(tag, str(match["tag"]))
                                         for tag in obj.get("tags", [])):
                matches.append(rule)
        if len(matches) > 1:
            raise ValueError(f"ambiguous workload expectations for {obj.get('canonical_id')}")
        return matches[0] if matches else None

    @staticmethod
    def _synthetic(rule_id, obj, severity, reason, action, evidence):
        return {"id": "virt:" + hashlib.sha256(
            f"{rule_id}|{obj.get('canonical_id')}".encode()).hexdigest()[:20],
            "rule_id": rule_id, "provider": obj.get("provider", ""),
            "deployment_id": obj.get("deployment_id", ""), "site_id": obj.get("site_id", ""),
            "canonical_id": obj.get("canonical_id", ""), "object_name": obj.get("display_name", ""),
            "severity": severity, "confidence": "high", "reason": reason,
            "recommended_operator_check": action, "evidence": evidence,
            "first_observed": obj.get("observed_at"), "last_observed": obj.get("observed_at")}

    def promote(self, state, now=None):
        now = now or datetime.now(timezone.utc)
        objects = {value["canonical_id"]: value for value in state.get("objects", [])}
        findings = list(state.get("findings", []))
        managers = {}
        for value in objects.values():
            if value.get("kind") == "manager":
                managers.setdefault(value.get("provider"), value)
        for collection in state.get("collections", []):
            if collection.get("result") == "success" and not collection.get("partial"):
                continue
            provider = collection.get("provider", "")
            obj = managers.get(provider) or {
                "canonical_id": "virt:collection:" + hashlib.sha256(
                    f"{provider}|{collection.get('endpoint', '')}".encode()).hexdigest()[:20],
                "provider": provider, "deployment_id": state.get("deployment_id", ""),
                "site_id": collection.get("site_id", ""), "display_name": provider,
                "observed_at": collection.get("last_attempt"), "kind": "manager"}
            category = str(collection.get("diagnostic_category") or "").casefold()
            if category in {"authentication", "auth"}:
                rule, severity = "virtualisation.collection_authentication", "High"
            elif category in {"permission", "permissions"}:
                rule, severity = "virtualisation.collection_permission", "Unknown"
            else:
                rule, severity = "virtualisation.collection_partial", "Unknown"
            findings.append(self._synthetic(rule, obj, severity,
                "Virtualisation collection did not produce complete trustworthy evidence.",
                "Review the collection diagnostic category and delegated read-only access.",
                {"diagnostic_category": category, "partial": collection.get("partial", False)}))
        for obj in objects.values():
            if obj.get("kind") not in {"vm", "container"}:
                continue
            expected = self._expectation(obj)
            if (expected and expected["expected_state"] == "running"
                    and obj.get("power_state") == "stopped"):
                findings.append(self._synthetic(
                    "virtualisation.expected_workload_stopped", obj,
                    str(expected.get("criticality", "high")).title(),
                    "A workload configured to run is observed stopped.",
                    "Confirm the outage or restore the workload through approved change control.",
                    {"expected_state": "running", "observed_state": "stopped",
                     "expectation": expected["match"]}))
        generated = _time(state.get("generated_at"))
        stale = generated is None or (now - generated).total_seconds() > self.stale_seconds
        if stale and objects:
            newest = max(objects.values(), key=lambda value: str(value.get("observed_at", "")))
            findings.append(self._synthetic(
                "virtualisation.evidence_stale", newest, "Unknown",
                "Virtualisation evidence is stale and cannot support a current healthy state.",
                "Restore collection and confirm workload state before clearing prior findings.",
                {"generated_at": state.get("generated_at"), "stale_after_seconds": self.stale_seconds}))
        items = []
        for finding in findings:
            mapping = MAPPING.get(finding.get("rule_id"))
            if not mapping:
                continue
            kind, severity, title = mapping
            # Preserve stronger canonical severity except for explicitly Unknown mappings.
            if severity != "Unknown":
                severity = finding.get("severity") if finding.get("severity") in {
                    "Critical", "High", "Medium", "Low", "Info"} else severity
            obj = objects.get(finding.get("canonical_id"), {})
            object_kind = obj.get("kind") or finding.get("object_kind", "")
            if finding.get("rule_id") in {
                    "virtualisation.host_disconnected",
                    "virtualisation.host_not_responding"}:
                severity = "High" if obj.get("cluster_id") else "Critical"
            if (finding.get("rule_id") == "virtualisation.storage_inaccessible"
                    and obj.get("shared") is not True):
                severity = "High"
            evidence = {**(finding.get("evidence") or {}),
                "source_finding_id": finding.get("id"), "health_state": severity,
                "source_collector": "virtualisation"}
            items.append(OperationalItem(
                kind=kind, rule_id=finding["rule_id"], title=title,
                category="Virtualisation", severity=severity,
                priority=priority(severity, 5 if kind == "issue" else 0),
                canonical_id=finding.get("canonical_id", ""),
                device=finding.get("object_name") or obj.get("display_name", ""),
                site_id=finding.get("site_id", ""), site=finding.get("site_id", ""),
                summary=finding.get("reason", title),
                recommended_action=finding.get("recommended_operator_check", ""),
                impact="Current impact is limited to the evidence stated; management-plane "
                       "failure alone does not prove workload outage.",
                reason=finding.get("reason", ""),
                suggested_action=finding.get("recommended_operator_check", ""),
                evidence=evidence, deployment_id=finding.get("deployment_id", ""),
                domain="virtualisation", provider=finding.get("provider", ""),
                object_kind=object_kind, object_id=finding.get("canonical_id", ""),
                cluster_id=obj.get("cluster_id", ""), host_id=obj.get("host_id", "")
                    or (obj.get("canonical_id", "") if object_kind == "host" else ""),
                workload_id=obj.get("canonical_id", "") if object_kind in {"vm", "container"} else "",
                confidence=finding.get("confidence", "medium"),
                source_finding_id=finding.get("id", ""),
                first_observed=finding.get("first_observed"),
                last_observed=finding.get("last_observed"),
                affected_service_ids=_service_ids(
                    finding["rule_id"], obj, evidence)))
        return sorted({value.id: value for value in items}.values(),
                      key=lambda value: (-value.priority, value.id))
