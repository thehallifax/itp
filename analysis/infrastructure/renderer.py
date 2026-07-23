"""Flat Grafana summary renderer."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from collectors.writer import atomic_write


def flat_summary(state):
    summary = state["summary"]
    health = ("Critical" if summary["critical"] or summary["collectors_failed"] else
              "Warning" if summary["offline"] or summary["warnings"] else "Healthy")
    return {
        "generated_at": state["generated_at"],
        "infrastructure_health": health,
        "sites": summary["sites"], "devices": summary["devices"],
        "devices_online": summary["online"], "devices_offline": summary["offline"],
        "warnings": summary["warnings"], "critical": summary["critical"],
        "collectors_healthy": summary["collectors_healthy"],
        "collectors_failed": summary["collectors_failed"],
        "switches_total": state["network"]["switches"]["total"],
        "switches_online": state["network"]["switches"]["online"],
        "switches_offline": state["network"]["switches"]["offline"],
        "aps_total": state["wireless"]["aps"]["total"],
        "aps_online": state["wireless"]["aps"]["online"],
        "aps_offline": state["wireless"]["aps"]["offline"],
        "firewalls_total": state["firewalls"]["firewalls"],
        "firewalls_healthy": state["firewalls"]["healthy"],
        "firewalls_offline": state["firewalls"]["offline"],
        "servers_total": state["servers"]["servers"],
        "servers_healthy": state["servers"]["healthy"],
        "servers_offline": state["servers"]["offline"],
        "printers_total": state["printers"]["total"],
        "printers_healthy": state["printers"]["healthy"],
        "printers_offline": state["printers"]["offline"],
    }


def write_state(output_dir, dashboard_dir, state):
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(output_dir / "state.json", json.dumps(state, indent=2, sort_keys=True) + "\n")
    fields = ("asset_id", "hostname", "device_type", "site", "management_ip", "online",
              "health", "collector", "serial_number")
    stream = io.StringIO(); writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader(); writer.writerows(state["assets"])
    atomic_write(output_dir / "state.csv", stream.getvalue())
    summary = flat_summary(state)
    atomic_write(Path(dashboard_dir) / "infrastructure-summary.json",
                 json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary
