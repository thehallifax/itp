"""Modular deterministic operational rules."""
from __future__ import annotations

from .models import OperationalItem, priority


def _name(asset):
    return str(asset.get("hostname") or asset.get("display_name") or asset.get("asset_id") or "Unknown device")


def _kind(asset):
    return " ".join(str(asset.get(key, "")).lower() for key in ("device_type", "device_role", "platform"))


def _site(asset):
    value = asset.get("site") or {}
    return str(value.get("display_name") or "") if isinstance(value, dict) else str(value)


def _site_id(asset):
    value = asset.get("site") or {}
    return str(value.get("site_id") or "") if isinstance(value, dict) else str(asset.get("site_id") or "")


def item(rule, kind, title, severity, *, device="", site="", site_id="", summary="", action="",
         impact="", reason="", evidence=None, weight=0, canonical_id=""):
    return OperationalItem(kind=kind, rule_id=rule.id, title=title, category=rule.category,
        severity=severity, priority=priority(severity, weight), canonical_id=canonical_id,
        device=device, site_id=site_id, site=site,
        summary=summary, recommended_action=action, impact=impact, reason=reason,
        suggested_action=action, evidence=evidence or {})


class Rule:
    id = ""
    category = "Inventory"
    _registry = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.id:
            Rule._registry.append(cls)

    @classmethod
    def registered(cls):
        return [rule() for rule in sorted(cls._registry, key=lambda value: value.id)]

    def evaluate(self, context):
        raise NotImplementedError


class CollectorOverdueRule(Rule):
    id = "collector.overdue"; category = "Collector"
    def evaluate(self, context):
        result = []; threshold = int(context.settings.get("collector_overdue_seconds", 900))
        for name, state in sorted(context.source_states.items()):
            run = state.get("last_run", {}); age = context.age_days(run.get("completed_at"))
            if age is not None and age * 86400 > threshold:
                hours = age * 24
                result.append(item(self, "risk", f"Collector overdue: {name}", "Medium", device=name,
                    summary=f"No completed {name} run for {hours:.1f} hours.",
                    reason=f"Latest run exceeded the configured {threshold}-second freshness threshold.",
                    impact="New device state and telemetry may be missing.",
                    action="Check collector scheduling, connectivity, and credentials.",
                    evidence={"age_seconds": round(age * 86400), "threshold_seconds": threshold}, weight=5))
        return result


class CollectorFailedRule(Rule):
    id = "collector.failed"; category = "Collector"
    def evaluate(self, context):
        result = []
        for name, state in sorted(context.source_states.items()):
            failures = int(state.get("consecutive_failures", 0)); run = state.get("last_run", {})
            if failures < 1 or run.get("success") is not False: continue
            reason = f"{name} failed {failures} consecutive run{'s' if failures != 1 else ''}."
            evidence = {"consecutive_failures": failures, "error_category": run.get("error_category")}
            result.append(item(self, "issue", f"Collector failed: {name}", "High", device=name,
                summary=reason, reason=reason, impact="Telemetry and inventory updates may be incomplete.",
                action="Verify API credentials, network reachability, and collector logs.", evidence=evidence,
                weight=min(15, failures * 3)))
            result.append(item(self, "risk", f"Telemetry coverage at risk: {name}", "Medium", device=name,
                summary=reason, reason="Repeated collection failures reduce operational visibility.",
                impact="Incidents may be detected late or not at all.",
                action="Restore the collector and confirm a successful complete run.", evidence=evidence, weight=8))
            result.append(item(self, "recommendation", f"Restore {name} collector", "High", device=name,
                summary="Restore reliable collection.", reason=reason,
                impact="Recovers telemetry freshness and inventory accuracy.",
                action="Verify API credentials first, then connectivity and collector logs.", evidence=evidence))
        return result


class DeviceOfflineRule(Rule):
    id = "device.offline"; category = "Network"
    def evaluate(self, context):
        result = []
        for asset in context.assets:
            kind = _kind(asset)
            if asset.get("online") is not False or any(x in kind for x in ("access-point", "switch", "firewall")): continue
            category = "Printing" if "print" in kind else "Server" if "server" in kind else "Network"
            rule = type("Bound", (), {"id": self.id, "category": category})()
            result.append(item(rule, "issue", f"Device offline: {_name(asset)}", "High",
                canonical_id=asset.get("canonical_id", ""),
                device=_name(asset), site=_site(asset), site_id=_site_id(asset), summary="Inventory reports the device offline.",
                reason="The latest complete observation explicitly reported online=false.",
                impact="The device may be unavailable to users or dependent services.",
                action="Confirm power and network reachability, then inspect the device.",
                evidence={"canonical_id": asset.get("canonical_id"), "online": False,
                          "sources": asset.get("sources", [])}))
        return result


class InventoryDriftRule(Rule):
    id = "inventory.drift"; category = "Inventory"
    def evaluate(self, context):
        result = []
        for record in context.reconciliations:
            if record.get("status") not in {"conflict", "ambiguous"}: continue
            ids = record.get("asset_ids", [])
            result.append(item(self, "risk", "Inventory reconciliation requires review", "Low",
                summary=f"{len(ids)} records have {record.get('status')} identity evidence.",
                reason="Multiple collector records cannot be deterministically reconciled.",
                impact="Asset counts and ownership may be inaccurate.",
                action="Review the records and correct stable identity data.",
                evidence={"asset_ids": ids, "status": record.get("status")}))
        return result


class FirmwareUnsupportedRule(Rule):
    id = "firmware.unsupported"; category = "Security"
    def evaluate(self, context):
        result = []; approved = context.signals.get("approved_firmware", {})
        for asset in context.assets:
            current = str(asset.get("firmware_version") or ""); target = approved.get(asset.get("model")) or approved.get(asset.get("vendor"))
            if not current or not target or current.startswith(str(target)): continue
            reason = f"Running {current}; approved release is {target}."
            result.extend([
                item(self, "risk", f"Unsupported firmware: {_name(asset)}", "High", canonical_id=asset.get("canonical_id", ""), device=_name(asset), site=_site(asset), site_id=_site_id(asset), summary=reason, reason=reason, impact="Security fixes and vendor support may be unavailable.", action=f"Plan an upgrade to approved release {target}.", evidence={"running": current, "approved": target, "sources": asset.get("sources", [])}),
                item(self, "recommendation", f"Upgrade firmware on {_name(asset)}", "High", canonical_id=asset.get("canonical_id", ""), device=_name(asset), site=_site(asset), site_id=_site_id(asset), summary="Move the device to an approved release.", reason=reason, impact="Restores supportability and security maintenance.", action=f"Validate and schedule upgrade to {target}.", evidence={"running": current, "approved": target, "sources": asset.get("sources", [])})])
        return result


class CertificateExpiryRule(Rule):
    id = "certificate.expiry"; category = "Security"
    def evaluate(self, context):
        result = []
        for cert in context.signals.get("certificates", []):
            days = context.age_days(cert.get("expires_at"))
            if days is None: continue
            days_remaining = -days if context.now.isoformat() > str(cert.get("expires_at")) else (context.now.__class__.fromisoformat(str(cert["expires_at"]).replace("Z", "+00:00")) - context.now).total_seconds()/86400
            if days_remaining > 30: continue
            severity = "Critical" if days_remaining <= 0 else "High" if days_remaining <= 7 else "Medium"
            name = str(cert.get("name") or cert.get("hostname") or "certificate")
            reason = f"Certificate expires in {max(0, int(days_remaining))} days."
            result.extend([item(self, "risk", f"Certificate expiry: {name}", severity, device=name, site=cert.get("site", ""), summary=reason, reason=reason, impact="TLS-dependent services may become unavailable or untrusted.", action="Renew and deploy the certificate before expiry.", evidence={"expires_at": cert.get("expires_at")}), item(self, "recommendation", f"Renew certificate: {name}", severity, device=name, site=cert.get("site", ""), summary="Renew the expiring certificate.", reason=reason, impact="Prevents trust warnings and service interruption.", action="Renew, deploy, and validate the certificate.", evidence={"expires_at": cert.get("expires_at")})])
        return result


class PrinterConsumablesRule(Rule):
    id = "printer.consumables"; category = "Printing"
    def evaluate(self, context):
        result = []
        for signal in context.signals.get("printer_consumables", []):
            level = signal.get("percent_remaining")
            if level is None or float(level) > 15: continue
            name = str(signal.get("device") or "printer"); supply = str(signal.get("supply") or "consumable")
            result.append(item(self, "recommendation", f"Replace {supply}: {name}", "Medium" if float(level) <= 5 else "Low", device=name, site=signal.get("site", ""), summary=f"{supply} is at {float(level):g}%.", reason="Consumable level is below the deterministic 15% threshold.", impact="Printing may stop when the supply is exhausted.", action=f"Order or replace the {supply}.", evidence={"percent_remaining": float(level), "threshold": 15}))
        return result


class WanPacketLossRule(Rule):
    id = "wan.packet_loss"; category = "Network"
    def evaluate(self, context):
        result = []
        for signal in context.signals.get("wan", []):
            loss = float(signal.get("packet_loss_percent", 0))
            if loss < 2: continue
            severity = "High" if loss >= 10 else "Medium"
            name = str(signal.get("name") or "WAN")
            result.extend([item(self, "issue", f"WAN packet loss: {name}", severity, device=name, site=signal.get("site", ""), summary=f"Packet loss is {loss:g}%.", reason="Packet loss exceeded the deterministic 2% threshold.", impact="Applications may be slow or unreliable.", action="Check the carrier circuit, edge interface, and path quality.", evidence={"packet_loss_percent": loss, "threshold": 2}), item(self, "recommendation", f"Investigate WAN packet loss: {name}", severity, device=name, site=signal.get("site", ""), summary="Investigate degraded WAN quality.", reason=f"Measured packet loss is {loss:g}%.", impact="Reducing loss improves application reliability.", action="Compare both path ends, interface errors, and carrier telemetry.", evidence={"packet_loss_percent": loss})])
        return result


class WanUnavailableRule(Rule):
    id = "wan.unavailable"; category = "Network"
    def evaluate(self, context):
        result = []
        for signal in context.signals.get("wan", []):
            if signal.get("available") is not False: continue
            name = str(signal.get("name") or "WAN")
            result.append(item(self, "issue", f"WAN unavailable: {name}", "Critical", device=name, site=signal.get("site", ""), summary="WAN availability is explicitly false.", reason="The latest path check failed.", impact="The site may have no external connectivity.", action="Validate edge equipment, circuit state, and carrier status.", evidence={"available": False}, weight=5))
        return result


class UnknownInventoryRule(Rule):
    id = "inventory.unknown"; category = "Inventory"
    def evaluate(self, context):
        result = []
        for asset in context.assets:
            missing = [key for key in ("vendor", "device_type") if not asset.get(key)]
            if not missing: continue
            result.append(item(self, "risk", f"Unknown inventory: {_name(asset)}", "Low",
                canonical_id=asset.get("canonical_id", ""), device=_name(asset), site=_site(asset), site_id=_site_id(asset),
                summary="Asset classification is incomplete.",
                reason="Missing required inventory attributes: " + ", ".join(missing) + ".",
                impact="Ownership, lifecycle, and health rules may be incomplete.",
                action="Run discovery and enrich the asset identity.",
                evidence={"canonical_id": asset.get("canonical_id"), "missing_fields": missing,
                          "sources": asset.get("sources", [])}))
        return result


class LifecycleStaleRule(Rule):
    id = "lifecycle.stale"; category = "Lifecycle"
    def evaluate(self, context):
        result = []
        for asset in context.assets:
            days = context.age_days(asset.get("last_seen_at"))
            if days is None or days < 30 or asset.get("lifecycle_state") == "retired": continue
            name = _name(asset); common = dict(canonical_id=asset.get("canonical_id", ""), device=name,
                site=_site(asset), site_id=_site_id(asset), evidence={"canonical_id": asset.get("canonical_id"),
                "days_not_seen": int(days), "last_seen_at": asset.get("last_seen_at"),
                "sources": asset.get("sources", [])})
            result.append(item(self, "risk", f"Device not seen for {int(days)} days: {name}", "Medium", summary="The asset has not been observed for at least 30 days.", reason=f"Last seen {int(days)} days ago.", impact="Inventory may include an absent or unmanaged device.", action="Run discovery and confirm whether the asset still exists.", **common))
            if days >= 60:
                result.append(item(self, "recommendation", f"Review asset for archival: {name}", "Low", summary="Review this long-unseen asset for retirement.", reason=f"The asset has not been seen for {int(days)} days.", impact="Archiving confirmed removals improves inventory accuracy.", action="Confirm decommissioning, then retire the asset through the inventory CLI.", **common))
        return result


class TypedOfflineRule(Rule):
    match = ""; title_word = "Device"; severity = "High"
    def evaluate(self, context):
        result = []
        for asset in context.assets:
            if asset.get("online") is not False or self.match not in _kind(asset): continue
            name = _name(asset)
            result.append(item(self, "issue", f"{self.title_word} offline: {name}", self.severity,
                canonical_id=asset.get("canonical_id", ""),
                device=name, site=_site(asset), site_id=_site_id(asset), summary=f"{self.title_word} is explicitly offline.",
                reason="The latest complete inventory observation reported online=false.",
                impact=f"Services dependent on this {self.title_word.lower()} may be unavailable.",
                action="Confirm power, uplink, and management reachability.",
                evidence={"canonical_id": asset.get("canonical_id"), "online": False,
                          "sources": asset.get("sources", [])}, weight=5))
        return result


class AccessPointOfflineRule(TypedOfflineRule):
    id = "wireless.ap_offline"; category = "Wireless"; match = "access-point"; title_word = "Access point"


class SwitchOfflineRule(TypedOfflineRule):
    id = "network.switch_offline"; category = "Network"; match = "switch"; title_word = "Switch"


class FirewallUnavailableRule(TypedOfflineRule):
    id = "firewall.unavailable"; category = "Firewall"; match = "firewall"; title_word = "Firewall"; severity = "Critical"
