"""Deterministic canonical service evaluators."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .models import STATUS_SEVERITY, ServiceHealth


def _text(asset):
    return " ".join(str(asset.get(key, "")).lower()
                    for key in ("device_type", "device_role", "platform", "model"))


def _asset_name(asset):
    return str(asset.get("hostname") or asset.get("display_name") or
               asset.get("canonical_id") or asset.get("asset_id") or "Unknown asset")


def _timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _latest(values):
    parsed = [item for item in (_timestamp(value) for value in values) if item]
    if not parsed:
        return None
    return max(parsed).isoformat().replace("+00:00", "Z")


def _finding_asset(value):
    return str(value.get("device") or value.get("canonical_id") or "")


def _affected_users(values):
    counts = []
    for value in values:
        evidence = value.get("evidence") or {}
        candidate = evidence.get("affected_users", value.get("affected_users"))
        if isinstance(candidate, (int, float)) and candidate >= 0:
            counts.append(int(candidate))
    return sum(counts) if counts else None


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class ServiceDefinition:
    name: str
    capability: str
    asset_terms: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    rule_prefixes: tuple[str, ...] = ()
    signal_keys: tuple[str, ...] = ()


DEFINITIONS = (
    ServiceDefinition("Internet", "internet", categories=("Network",),
                      rule_prefixes=("wan.",), signal_keys=("wan",)),
    ServiceDefinition("Wireless", "wireless", ("access-point", "wireless"),
                      ("Wireless",), signal_keys=("wireless",)),
    ServiceDefinition("Switching", "switching", ("switch",), ("Network",),
                      rule_prefixes=("network.switch",)),
    ServiceDefinition("Printing", "printing", ("print",), ("Printing",),
                      signal_keys=("printer_consumables", "printing")),
    ServiceDefinition("Identity", "identity", ("domain-controller", "identity"),
                      signal_keys=("identity", "authentication")),
    ServiceDefinition("Compute", "compute", ("server", "hypervisor"), ("Server",),
                      signal_keys=("compute",)),
    ServiceDefinition("Storage", "storage", ("storage", "san", "nas"), ("Storage",),
                      signal_keys=("storage",)),
    ServiceDefinition("Voice", "voice", ("voice", "phone", "sip"),
                      signal_keys=("voice",)),
    ServiceDefinition("Email", "email", ("email", "mail"),
                      signal_keys=("email",)),
    ServiceDefinition("Security", "firewall", ("firewall", "security-appliance"),
                      ("Firewall", "Security"), signal_keys=("security",)),
    ServiceDefinition("Monitoring", "telemetry", categories=("Collector",),
                      signal_keys=("monitoring",)),
    ServiceDefinition("Virtualisation Management Plane", "virtualisation",
                      ("virtualisation-manager",)),
    ServiceDefinition("Hypervisor Cluster", "virtualisation",
                      ("virtualisation-cluster", "hypervisor")),
    ServiceDefinition("Compute Capacity", "virtualisation",
                      ("virtualisation-cluster", "hypervisor")),
    ServiceDefinition("Virtual Machine Hosting", "virtualisation",
                      ("hypervisor", "compute-workload")),
    ServiceDefinition("Shared Storage", "virtualisation", ("virtual-storage",)),
    ServiceDefinition("Workload Availability", "virtualisation",
                      ("compute-workload",)),
)

VIRTUAL_SERVICE_IDS = {
    "Virtualisation Management Plane": "virtualisation_management_plane",
    "Hypervisor Cluster": "hypervisor_cluster",
    "Compute Capacity": "compute_capacity",
    "Virtual Machine Hosting": "virtual_machine_hosting",
    "Shared Storage": "shared_storage",
    "Workload Availability": "workload_availability",
}


class ServiceEvaluator:
    """Evaluator registry with one deterministic evaluator per canonical service."""

    _registry = {}
    definition = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.definition:
            ServiceEvaluator._registry[cls.definition.name] = cls

    @classmethod
    def registered(cls, include_virtualisation=False):
        names = sorted(cls._registry)
        if not include_virtualisation:
            names = [name for name in names if name not in VIRTUAL_SERVICE_IDS]
        return [cls._registry[name]() for name in names]

    @staticmethod
    def _matches_finding(definition, value):
        rule = str(value.get("rule_id") or "").lower()
        category = str(value.get("category") or "")
        if any(rule.startswith(prefix.lower()) for prefix in definition.rule_prefixes):
            return True
        if category not in definition.categories:
            return False
        if definition.name == "Internet":
            return rule.startswith("wan.")
        if definition.name == "Switching":
            text = " ".join(str(value.get(key, "")).lower()
                            for key in ("title", "device", "summary"))
            return "switch" in text
        return True

    def evaluate(self, context):
        definition = self.definition
        if definition.capability not in context["capabilities"]:
            return ServiceHealth(definition.name, "Not Enabled",
                f"{definition.name} capability is not enabled.",
                severity=STATUS_SEVERITY["Not Enabled"],
                evidence=({"type": "capability", "capability": definition.capability,
                           "enabled": False},))
        declared = []
        for collector, manifest in sorted(
                context.get("capability_manifest", {}).items()):
            if collector not in context.get("enabled_collectors", ()):
                continue
            for value in manifest.get("capabilities", []):
                if definition.name in value.get("services", []):
                    declared.append((collector, value))
        applicable = [(collector, value) for collector, value in declared
                      if value.get("support") != "unsupported"]
        failed = [(collector, value) for collector, value in applicable
                  if value.get("collection") == "failed"]
        degraded = [(collector, value) for collector, value in applicable
                    if value.get("collection") in {"partial", "unavailable"}]
        capability_evidence = tuple({
            "type": "collector_capability", "collector": collector,
            "capability": value.get("id"), "support": value.get("support"),
            "collection": value.get("collection"),
            "explanation": value.get("explanation"),
        } for collector, value in declared)
        if failed:
            return ServiceHealth(
                definition.name, "Critical",
                f"Required {definition.name.lower()} collection failed.",
                tuple(sorted({collector for collector, _ in failed})),
                severity=STATUS_SEVERITY["Critical"],
                evidence=capability_evidence)
        if degraded and not any(value.get("collection") == "collected"
                                for _, value in applicable):
            return ServiceHealth(
                definition.name, "Warning",
                f"{definition.name} evidence is incomplete or unavailable.",
                tuple(sorted({collector for collector, _ in degraded})),
                severity=STATUS_SEVERITY["Warning"],
                evidence=capability_evidence)
        if definition.name == "Internet":
            return self._internet(context)
        if definition.name == "Security":
            return self._security(context)
        if definition.name in VIRTUAL_SERVICE_IDS:
            return self._virtualisation(context)

        assets = [value for value in context["assets"]
                  if any(term in _text(value) for term in definition.asset_terms)]
        findings = [value for value in context["findings"]
                    if self._matches_finding(definition, value)]
        signals = []
        for key in definition.signal_keys:
            value = context["signals"].get(key)
            if isinstance(value, list):
                signals.extend({"signal": key, **item} if isinstance(item, dict)
                               else {"signal": key, "value": item} for item in value)
            elif value is not None:
                signals.append({"signal": key, **value} if isinstance(value, dict)
                               else {"signal": key, "value": value})

        collector_evidence = []
        if definition.name == "Monitoring":
            collector_evidence = list(context["collectors"])

        critical_findings = [value for value in findings
                             if value.get("kind") == "issue"
                             and value.get("severity") in {"Critical", "High"}]
        warning_findings = [value for value in findings if value not in critical_findings]
        offline = [value for value in assets
                   if value.get("online") is False or str(value.get("status", "")).lower() == "offline"]
        warning_assets = [value for value in assets if str(
            value.get("health") or value.get("status") or "").lower() in {"warning", "degraded"}]

        explicit_down = [value for value in signals
                         if value.get("available") is False
                         or str(value.get("status", "")).lower() in {"critical", "failed", "offline", "down"}]
        explicit_warning = [value for value in signals
                            if str(value.get("status", "")).lower() in {"warning", "degraded"}
                            or _number(value.get("packet_loss_percent")) >= 2]
        failed_collectors = [value for value in collector_evidence
                             if str(value.get("status", "")).lower() == "failed"]
        warning_collectors = [value for value in collector_evidence
                              if str(value.get("status", "")).lower() in {"warning", "stale", "unknown"}]

        if critical_findings or offline or explicit_down or failed_collectors:
            status = "Critical"
        elif warning_findings or warning_assets or explicit_warning or warning_collectors:
            status = "Warning"
        elif assets or signals or collector_evidence:
            status = "Healthy"
        else:
            status = "Unknown"

        affected = sorted({_asset_name(value) for value in offline + warning_assets}
                          | {_finding_asset(value) for value in findings if _finding_asset(value)})
        evidence = []
        for value in sorted(findings, key=lambda item: (
                -int(item.get("priority", 0)), str(item.get("rule_id", "")), str(item.get("id", "")))):
            evidence.append({"type": "finding", "id": value.get("id", ""),
                "rule_id": value.get("rule_id", ""), "kind": value.get("kind", ""),
                "severity": value.get("severity", ""), "summary": value.get("summary", ""),
                "site_id": value.get("site_id", ""), "site_name": value.get("site_name", "")})
        for value in sorted(assets, key=_asset_name):
            evidence.append({"type": "asset", "asset": _asset_name(value),
                "canonical_id": value.get("canonical_id", ""),
                "site_id": value.get("site_id") or (
                    value.get("site", {}).get("site_id")
                    if isinstance(value.get("site"), dict) else ""),
                "site_name": value.get("site_name") or (
                    value.get("site", {}).get("display_name")
                    if isinstance(value.get("site"), dict) else ""),
                "status": value.get("status",
                    "online" if value.get("online") is True
                    else "offline" if value.get("online") is False else "unknown")})
        for value in sorted(signals, key=lambda item: (
                str(item.get("signal", "")), str(item.get("name", "")), str(item.get("site", "")))):
            evidence.append({"type": "signal", **value})
        for value in sorted(collector_evidence, key=lambda item: str(item.get("collector", ""))):
            evidence.append({"type": "collector", "collector": value.get("collector", ""),
                "status": value.get("status", "unknown"), "last_run": value.get("last_run")})

        count = len(affected)
        summaries = {
            "Critical": f"{definition.name} has {count or len(critical_findings) or 1} active critical condition(s).",
            "Warning": f"{definition.name} has {count or len(warning_findings) or 1} condition(s) requiring attention.",
            "Healthy": f"{definition.name} has positive evidence and no active degradation.",
            "Unknown": f"No trustworthy {definition.name.lower()} health evidence is available.",
        }
        severity = STATUS_SEVERITY[status]
        if status in {"Warning", "Critical"}:
            ranked = {"Info": 0, "Low": 1, "Medium": 2, "High": 3, "Critical": 4}
            candidates = [str(value.get("severity") or "Info") for value in findings]
            if offline or explicit_down or failed_collectors:
                candidates.append("Critical")
            elif warning_assets or explicit_warning or warning_collectors:
                candidates.append("Medium")
            severity = max(candidates, key=lambda value: ranked.get(value, 0))
        last_change = _latest(
            [value.get(key) for value in assets
             for key in ("last_changed_at", "last_seen_at")]
            + [value.get(key) for value in signals
               for key in ("last_change", "observed_at", "timestamp", "time")]
            + [value.get("last_run") for value in collector_evidence])
        return ServiceHealth(definition.name, status, summaries[status], tuple(affected),
            _affected_users(findings + signals), severity, last_change,
            tuple(evidence))

    def _virtualisation(self, context):
        definition = self.definition
        service_id = VIRTUAL_SERVICE_IDS[definition.name]
        assets = [value for value in context["assets"]
                  if any(term in _text(value) for term in definition.asset_terms)]
        findings = [value for value in context["findings"]
                    if service_id in value.get("affected_service_ids", [])]
        confirmed = [value for value in findings
                     if value.get("severity") == "Critical"
                     and value.get("kind") == "issue"]
        warnings = [value for value in findings
                    if value.get("severity") in {"High", "Medium", "Low"}]
        unknown = [value for value in findings
                   if value.get("severity") == "Unknown"
                   or (value.get("evidence") or {}).get("health_state") == "Unknown"]
        if confirmed:
            status = "Critical"
        elif warnings:
            status = "Warning"
        elif unknown:
            status = "Unknown"
        elif assets:
            status = "Healthy"
        else:
            status = "Unknown"
        affected = tuple(sorted({_finding_asset(value) for value in findings
                                 if _finding_asset(value)}))
        evidence = tuple({"type": "finding", "id": value.get("id", ""),
            "rule_id": value.get("rule_id", ""), "severity": value.get("severity", ""),
            "confidence": value.get("confidence", ""),
            "summary": value.get("summary", ""), "site_id": value.get("site_id", "")}
            for value in sorted(findings, key=lambda item: (
                -int(item.get("priority", 0)), str(item.get("id", "")))))
        if not evidence:
            evidence = tuple({"type": "asset", "canonical_id": value.get("canonical_id", ""),
                "asset": _asset_name(value), "observed_at": value.get("last_seen_at")
                or value.get("source_last_seen_at")} for value in sorted(assets, key=_asset_name))
        summaries = {
            "Critical": f"{definition.name} has confirmed operational impact.",
            "Warning": f"{definition.name} has evidence requiring attention.",
            "Unknown": f"{definition.name} cannot be evaluated from current trustworthy evidence.",
            "Healthy": f"{definition.name} has current evidence and no active degradation.",
        }
        return ServiceHealth(definition.name, status, summaries[status], affected,
            severity=STATUS_SEVERITY[status],
            last_change=_latest([value.get("last_observed") for value in findings]
                                + [value.get("last_seen_at") or
                                   value.get("source_last_seen_at") for value in assets]),
            evidence=evidence)

    @staticmethod
    def _internet(context):
        now = context.get("now") or datetime.now(timezone.utc)
        signals = [value for value in context["signals"].get("wan", [])
                   if isinstance(value, dict)
                   and value.get("classification_authoritative") is True]
        if not signals:
            return ServiceHealth("Internet", "Unknown",
                "No explicitly configured WAN interfaces provide trustworthy evidence.",
                severity=STATUS_SEVERITY["Unknown"],
                evidence=({"type": "wan_policy", "configured": False},))
        stale = []
        unknown = []
        for value in signals:
            observed = _timestamp(value.get("observed_at") or value.get("time"))
            if observed is None or (now - observed).total_seconds() > 300:
                stale.append(value)
            if value.get("available") is None:
                unknown.append(value)
        evidence = tuple({"type": "wan_interface",
            "interface": value.get("interface_name") or value.get("name"),
            "display_name": value.get("name"), "role": value.get("role"),
            "available": value.get("available"),
            "observed_at": value.get("observed_at")}
            for value in sorted(signals, key=lambda item: (
                str(item.get("role", "")), str(item.get("interface_name", "")))))
        if stale or unknown:
            return ServiceHealth("Internet", "Unknown",
                "WAN interface evidence is stale or incomplete.",
                tuple(sorted(str(value.get("name") or value.get("interface_name"))
                             for value in stale + unknown)),
                severity=STATUS_SEVERITY["Unknown"], evidence=evidence)
        down = [value for value in signals if value.get("available") is False]
        up = [value for value in signals if value.get("available") is True]
        primary_down = any(value.get("role") == "primary" for value in down)
        backups_up = any(value.get("role") in {
            "secondary", "tertiary", "backup", "cellular", "mpls", "internet", "other"}
            for value in up)
        if down and not up:
            status = "Critical"
            summary = "All explicitly configured Internet uplinks are down."
        elif primary_down and backups_up:
            status = "Warning"
            summary = "The primary Internet uplink is down; a configured backup remains up."
        elif down:
            status = "Warning"
            summary = "One or more configured Internet uplinks are down."
        else:
            status = "Healthy"
            summary = "All explicitly configured Internet uplinks are operational."
        return ServiceHealth("Internet", status, summary,
            tuple(sorted(str(value.get("name") or value.get("interface_name"))
                         for value in down)),
            severity=STATUS_SEVERITY[status],
            last_change=_latest(value.get("observed_at") for value in signals),
            evidence=evidence)

    @staticmethod
    def _security(context):
        signals = [value for value in context["signals"].get("security", [])
                   if isinstance(value, dict)]
        firewalls = [value for value in context["assets"]
                     if "firewall" in _text(value)]
        evidence = [{"type": "asset", "asset": _asset_name(value),
            "status": "online" if value.get("online") is True
                      else "offline" if value.get("online") is False else "unknown"}
            for value in firewalls]
        affected = set()
        critical = False
        warning = False
        findings = [value for value in context["findings"]
                    if value.get("category") in {"Firewall", "Security"}]
        for value in findings:
            severity = str(value.get("severity") or "Info")
            if severity in {"Critical", "High"} and value.get("kind") == "issue":
                critical = True
            else:
                warning = True
            device = _finding_asset(value)
            if device:
                affected.add(device)
            evidence.append({"type": "finding", "id": value.get("id"),
                "rule_id": value.get("rule_id"), "severity": severity,
                "summary": value.get("summary")})
        for value in signals:
            device = str(value.get("device") or "Firewall")
            certificate = value.get("device_certificate") or {}
            classification = str(certificate.get("classification") or "unknown").lower()
            evidence.append({"type": "device_certificate", "device": device,
                "classification": classification, "status": certificate.get("status")})
            if classification in {"expired", "missing"}:
                critical = True; affected.add(device)
            elif classification == "unknown":
                warning = True
            for licence in value.get("licenses") or []:
                evidence.append({"type": "subscription", "device": device,
                    "name": licence.get("name"), "expiry_state": licence.get("expiry_state"),
                    "expiry": licence.get("expiry")})
                if licence.get("expired") is True:
                    warning = True; affected.add(device)
        offline = [value for value in firewalls if value.get("online") is False]
        if offline:
            critical = True
            affected.update(_asset_name(value) for value in offline)
        if critical:
            status, summary = "Critical", "Firewall availability or device-certificate evidence is critical."
        elif warning:
            status, summary = "Warning", "Security subscriptions or certificate evidence requires attention."
        elif signals or firewalls:
            status, summary = "Healthy", "Firewall, subscription and certificate evidence has no active degradation."
        else:
            status, summary = "Unknown", "No trustworthy security health evidence is available."
        return ServiceHealth("Security", status, summary, tuple(sorted(affected)),
            severity=STATUS_SEVERITY[status],
            last_change=_latest(
                [value.get("observed_at") for value in signals]
                + [value.get(key) for value in firewalls
                   for key in ("last_changed_at", "last_seen_at")]),
            evidence=tuple(evidence))


def _register_evaluators():
    for definition in DEFINITIONS:
        type(definition.name.replace(" ", "") + "Evaluator", (ServiceEvaluator,),
             {"definition": definition})


_register_evaluators()
