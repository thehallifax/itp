"""Deterministic notification incident evaluation and delivery."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .channels import NotificationChannelRegistry
from .models import NotificationDelivery, NotificationDeliveryStatus
from .rules import evaluate_conditions
from .store import NotificationStore


SEVERITY_ORDER = {"info": 0, "recovery": 0, "warning": 1, "critical": 2}


def _utc(value=None):
    value = value or datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _event_id(fingerprint, first_seen, recovery=False):
    material = f"{fingerprint}|{first_seen}|{'recovery' if recovery else 'active'}"
    return "notification:" + hashlib.sha256(material.encode()).hexdigest()[:24]


class NotificationEngine:
    def __init__(self, runtime_dir, config=None, *, channel_registry=None,
                 now_fn=None):
        self.config = dict(config or {})
        self.store = NotificationStore(runtime_dir)
        self.channels = channel_registry or NotificationChannelRegistry()
        self.now = now_fn or (lambda: datetime.now(timezone.utc))

    @property
    def enabled(self):
        return self.config.get("enabled") is True

    def _minimum(self):
        value = str(self.config.get("minimum_severity") or "warning").lower()
        return SEVERITY_ORDER.get(value, SEVERITY_ORDER["warning"])

    def _deliver(self, state, event, *, force=False):
        now = _utc(self.now())
        repeat = max(
            0, int(self.config.get("repeat_suppression_seconds", 3600)))
        deliveries = []
        for channel in self.channels.enabled(self.config):
            previous = [
                value for value in state["deliveries"]
                if value.get("event_id") == event["id"]
                and value.get("channel") == channel.id
                and value.get("status") in {"delivered", "failed"}]
            suppressed = False
            if previous and not force:
                last = datetime.fromisoformat(
                    previous[-1]["attempted_at"].replace("Z", "+00:00"))
                suppressed = (
                    self.now().astimezone(timezone.utc)
                    - last.astimezone(timezone.utc)).total_seconds() < repeat
            status = NotificationDeliveryStatus.SUPPRESSED.value \
                if suppressed else NotificationDeliveryStatus.PENDING.value
            exception_type = ""
            detail = ""
            delivered_at = None
            if not suppressed:
                try:
                    channel.deliver(event)
                    status = NotificationDeliveryStatus.DELIVERED.value
                    delivered_at = now
                except Exception as exc:
                    status = NotificationDeliveryStatus.FAILED.value
                    exception_type = type(exc).__name__
                    detail = "delivery failed"
            delivery_id = "delivery:" + hashlib.sha256(
                f"{event['id']}|{channel.id}|{now}|{len(state['deliveries'])}".encode()
            ).hexdigest()[:24]
            delivery = NotificationDelivery(
                delivery_id, event["id"], channel.id, status, now,
                delivered_at, exception_type, detail).to_dict()
            state["deliveries"].append(delivery)
            deliveries.append(delivery)
        return deliveries

    def evaluate(self, status, doctor=None):
        if not self.enabled:
            return {
                "enabled": False, "new_events": [], "recoveries": [],
                "deliveries": [], "active_count": 0}
        state = self.store.read()
        now = _utc(self.now())
        conditions = {
            value["fingerprint"]: value for value in evaluate_conditions(
                status, doctor, now=self.now(),
                heartbeat_stale_seconds=int(self.config.get(
                    "daemon_heartbeat_stale_seconds", 30)))}
        if doctor is None:
            for fingerprint, incident in state["active"].items():
                if incident.get("source") == "doctor":
                    conditions.setdefault(fingerprint, {
                        key: incident[key] for key in (
                            "fingerprint", "rule_id", "severity", "title",
                            "summary", "source", "subject")})
                    conditions[fingerprint]["_preserve_only"] = True
        minimum = self._minimum()
        conditions = {
            key: value for key, value in conditions.items()
            if SEVERITY_ORDER[value["severity"]] >= minimum}
        new_events, recoveries, deliveries = [], [], []
        previous_active = dict(state["active"])
        for fingerprint, condition in sorted(conditions.items()):
            existing = state["active"].get(fingerprint)
            if existing:
                if condition.get("_preserve_only"):
                    continue
                existing["last_seen"] = now
                existing["occurrence_count"] = int(
                    existing.get("occurrence_count", 1)) + 1
                state["active"][fingerprint] = existing
                for index, event in enumerate(state["events"]):
                    if event.get("id") == existing["id"]:
                        state["events"][index] = dict(existing)
                if not existing.get("acknowledged"):
                    deliveries.extend(self._deliver(state, existing))
                continue
            event = {
                "schema_version": 1,
                "id": _event_id(fingerprint, now),
                **condition, "first_seen": now, "last_seen": now,
                "occurrence_count": 1, "active": True,
                "acknowledged": False, "recovery_of": "", "test": False,
            }
            state["active"][fingerprint] = event
            state["events"].append(event)
            new_events.append(event)
            deliveries.extend(self._deliver(state, event, force=True))
        for fingerprint, incident in sorted(previous_active.items()):
            if fingerprint in conditions:
                continue
            incident["active"] = False
            incident["last_seen"] = now
            for index, event in enumerate(state["events"]):
                if event.get("id") == incident["id"]:
                    state["events"][index] = dict(incident)
            state["active"].pop(fingerprint, None)
            recovery = {
                "schema_version": 1,
                "id": _event_id(fingerprint, now, recovery=True),
                "fingerprint": fingerprint,
                "rule_id": incident["rule_id"] + ".recovery",
                "severity": "recovery",
                "title": incident["title"] + " recovered",
                "summary": f"Recovered: {incident['summary']}",
                "source": incident["source"], "subject": incident["subject"],
                "first_seen": now, "last_seen": now, "occurrence_count": 1,
                "active": False, "acknowledged": False,
                "recovery_of": incident["id"], "test": False,
            }
            state["events"].append(recovery)
            recoveries.append(recovery)
            deliveries.extend(self._deliver(state, recovery, force=True))
        self.store.write(state)
        return {
            "enabled": True, "new_events": new_events,
            "recoveries": recoveries, "deliveries": deliveries,
            "active_count": len(state["active"]),
        }

    def test(self):
        now = _utc(self.now())
        event = {
            "schema_version": 1,
            "id": _event_id("test", now),
            "fingerprint": "test", "rule_id": "notifications.test",
            "severity": "info", "title": "ITP test notification",
            "summary": "This is a clearly marked ITP notification test.",
            "source": "cli", "subject": "notification-delivery",
            "first_seen": now, "last_seen": now, "occurrence_count": 1,
            "active": False, "acknowledged": False,
            "recovery_of": "", "test": True,
        }
        state = self.store.read()
        deliveries = self._deliver(state, event, force=True)
        self.store.write(state)
        return {
            "enabled": self.enabled, "event": event,
            "deliveries": deliveries}

    def summary(self):
        state = self.store.read()
        active = list(state["active"].values())
        events = state["events"]
        delivered = [
            value for value in state["deliveries"]
            if value.get("status") == "delivered"]
        return {
            "enabled": self.enabled,
            "active_count": len(active),
            "highest_active_severity": max(
                (value["severity"] for value in active),
                key=lambda value: SEVERITY_ORDER.get(value, -1),
                default=None),
            "most_recent_notification": events[-1] if events else None,
            "most_recent_successful_delivery": (
                delivered[-1] if delivered else None),
            "failed_delivery_count": sum(
                value.get("status") == "failed"
                for value in state["deliveries"]),
        }
