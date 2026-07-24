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
    estate_payload = {
        "schema_version": result.get("schema_version", 2),
        "generated_at": result.get("generated_at"),
        "deployment_id": result.get("deployment_id", ""),
        **result["estate"],
    }
    atomic_write(output_dir / "estate-health.json",
                 json.dumps(estate_payload, indent=2, sort_keys=True) + "\n")
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, extrasaction="ignore")
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
    estate_stream = io.StringIO()
    estate_fields = ("deployment_id", "service_id", "service", "state", "confidence",
        "affected_site_count", "total_site_count", "affected_site_ids",
        "last_evaluated", "summary")
    estate_writer = csv.DictWriter(estate_stream, fieldnames=estate_fields,
                                   extrasaction="ignore")
    estate_writer.writeheader()
    for value in result["estate"]["services"]:
        estate_writer.writerow({**value,
            "affected_site_ids": ";".join(value.get("affected_site_ids", []))})
    atomic_write(output_dir / "estate-health.csv", estate_stream.getvalue())
