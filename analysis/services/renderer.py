"""Atomic JSON and CSV output for canonical service health."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from collectors.writer import atomic_write


CSV_FIELDS = (
    "scope", "site_id", "site_name", "overall_status", "service", "status",
    "summary", "affected_assets", "affected_users", "severity", "last_change",
    "evidence",
)


def write_service_health(output_dir, result):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(output_dir / "service-health.json",
                 json.dumps(result, indent=2, sort_keys=True) + "\n")
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
    writer.writeheader()
    scopes = [result["estate"], *result["sites"]]
    scopes.sort(key=lambda value: value["site_id"])
    for scope in scopes:
        for value in scope["services"]:
            writer.writerow({"scope": "estate" if scope["site_id"] == "all" else "site",
                "site_id": scope["site_id"], "site_name": scope["site_name"],
                "overall_status": scope["overall_status"], **value,
                "affected_assets": json.dumps(value["affected_assets"], separators=(",", ":")),
                "evidence": json.dumps(value["evidence"], sort_keys=True, separators=(",", ":")),
                "affected_users": "" if value["affected_users"] is None else value["affected_users"]})
    atomic_write(output_dir / "service-health.csv", stream.getvalue())
