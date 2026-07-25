"""Atomic deterministic JSON/CSV rendering for virtualisation state."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from collectors.writer import atomic_write
from .telemetry import points


KINDS = {
    "platforms": {"platform", "manager"},
    "clusters": {"cluster"},
    "hosts": {"host"},
    "workloads": {"vm", "container"},
    "storage": {"storage"},
    "networks": {"network"},
    "snapshots": {"snapshot"},
}


def _write_csv(path, values):
    if not values:
        atomic_write(path, "\n")
        return
    fields = sorted({key for value in values for key in value
                     if key not in {"evidence", "tags", "ip_addresses", "mac_addresses",
                                    "network_ids", "storage_ids", "uplink_evidence"}})
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for value in values:
        writer.writerow({key: json.dumps(item, sort_keys=True, separators=(",", ":"))
                         if isinstance(item, (list, dict)) else item
                         for key, item in value.items()})
    atomic_write(path, stream.getvalue())


def render(output_dir, state):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    objects = state["objects"]
    for name, kinds in KINDS.items():
        values = [value for value in objects if value["kind"] in kinds]
        payload = {"schema_version": 1, "generated_at": state["generated_at"],
                   "deployment_id": state["deployment_id"], name: values}
        atomic_write(output_dir / f"{name}.json",
                     json.dumps(payload, indent=2, sort_keys=True) + "\n")
        if name in {"clusters", "hosts", "workloads", "storage"}:
            _write_csv(output_dir / f"{name}.csv", values)
    findings_payload = {"schema_version": 1, "generated_at": state["generated_at"],
        "deployment_id": state["deployment_id"], "findings": state["findings"]}
    atomic_write(output_dir / "findings.json",
                 json.dumps(findings_payload, indent=2, sort_keys=True) + "\n")
    _write_csv(output_dir / "findings.csv", state["findings"])
    atomic_write(output_dir / "summary.json",
                 json.dumps(state["summary"], indent=2, sort_keys=True) + "\n")
    atomic_write(output_dir / "collection-status.json", json.dumps({
        "schema_version": 1, "generated_at": state["generated_at"],
        "deployment_id": state["deployment_id"],
        "collections": state["collections"]}, indent=2, sort_keys=True) + "\n")
    atomic_write(output_dir / "assets.json", json.dumps({
        "schema_version": 1, "generated_at": state["generated_at"],
        "deployment_id": state["deployment_id"],
        "assets": state["assets"]}, indent=2, sort_keys=True) + "\n")
    atomic_write(output_dir / "telemetry.json", json.dumps({
        "schema_version": 1, "generated_at": state["generated_at"],
        "deployment_id": state["deployment_id"], "points": points(state),
    }, indent=2, sort_keys=True) + "\n")
    return state
