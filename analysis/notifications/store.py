"""Atomic filesystem persistence for notification incidents and deliveries."""
import json
from pathlib import Path

from collectors.writer import atomic_write


class NotificationStore:
    def __init__(self, runtime_dir):
        self.root = Path(runtime_dir) / "notifications"
        self.state_path = self.root / "state.json"

    def read(self):
        try:
            value = json.loads(self.state_path.read_text())
            if isinstance(value, dict):
                return value
        except (OSError, json.JSONDecodeError):
            pass
        return {
            "schema_version": 1, "active": {}, "events": [],
            "deliveries": []}

    def write(self, value):
        value = {
            "schema_version": 1,
            "active": dict(sorted(value.get("active", {}).items())),
            "events": sorted(value.get("events", []), key=lambda item: (
                item.get("first_seen", ""), item.get("id", ""))),
            "deliveries": sorted(value.get("deliveries", []), key=lambda item: (
                item.get("attempted_at", ""), item.get("id", ""))),
        }
        atomic_write(
            self.state_path, json.dumps(value, indent=2, sort_keys=True) + "\n")
        return value

    def find(self, notification_id):
        state = self.read()
        for value in state["events"]:
            if value.get("id") == notification_id:
                return value
        return None

    def acknowledge(self, notification_id, at):
        state = self.read()
        found = None
        for value in state["events"]:
            if value.get("id") == notification_id:
                value["acknowledged"] = True
                value["acknowledged_at"] = at
                found = value
        for key, value in state["active"].items():
            if value.get("id") == notification_id:
                value["acknowledged"] = True
                value["acknowledged_at"] = at
                state["active"][key] = value
        if found is not None:
            import hashlib
            state["deliveries"].append({
                "schema_version": 1,
                "id": "delivery:" + hashlib.sha256(
                    f"{notification_id}|acknowledged|{at}".encode()
                ).hexdigest()[:24],
                "event_id": notification_id, "channel": "operator",
                "status": "acknowledged", "attempted_at": at,
                "delivered_at": at, "exception_type": "", "detail": "",
            })
            self.write(state)
        return found
