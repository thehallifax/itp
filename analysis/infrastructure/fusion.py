"""Deterministic multi-source physical-asset fusion."""
from __future__ import annotations

from datetime import datetime, timezone

from .identity import (canonical_id, compatible_types, normalize_device_type, normalize_hostname,
                       normalize_ip, normalize_mac, normalize_serial, normalize_site, normalized,
                       short_hostname)
from .policy import STATUS_FRESHNESS_SECONDS, VENDOR_SOURCES, source_priority


CONFIDENCE_RANK = {"unmerged": 0, "low": 1, "medium": 2, "high": 3, "exact": 4}
FUSION_FIELDS = ("hostname", "serial_number", "management_ip", "site", "site_id",
    "site_display_name", "device_type",
    "device_role", "vendor", "model", "firmware_version", "lifecycle_state", "managed",
    "deployment_id", "customer_id", "customer",
    "location", "last_seen_at", "extensions")


def _source(record): return str(record.get("source") or record.get("collector") or "unknown").lower()


def _observed(record):
    value = record.get("source_last_seen_at") or record.get("last_seen_at") or record.get("observed_at")
    try: return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError): return None


def _site_alias(left, right):
    left = normalize_site(left); right = normalize_site(right)
    if not left or not right: return False
    return left == right or ((" " in left) != (" " in right))


def classify_match(left, right):
    a = normalized(left); b = normalized(right)
    sources = {_source(left), _source(right)}
    hostname_match = bool(a["short_hostname"] and a["short_hostname"] == b["short_hostname"])
    ip_match = bool(a["management_ip"] and a["management_ip"] == b["management_ip"])
    hardware_match = bool(
        (a["serial"] and a["serial"] == b["serial"])
        or (a["chassis_id"] and a["chassis_id"] == b["chassis_id"])
        or (a["management_mac"] and a["management_mac"] == b["management_mac"])
    )
    if not (hardware_match or hostname_match or ip_match):
        return "unmerged", [], ""
    if a["serial"] and b["serial"] and a["serial"] != b["serial"]:
        return "low", ["hostname" if hostname_match else "management_ip" if ip_match else "hardware"], \
            "conflicting serial numbers"
    if not compatible_types(a["device_type"], b["device_type"]):
        return "low", ["hostname" if hostname_match else "management_ip" if ip_match else "hardware"], \
            "incompatible device types"
    if a["serial"] and a["serial"] == b["serial"]: return "exact", ["serial_number"], ""
    if a["chassis_id"] and a["chassis_id"] == b["chassis_id"]: return "exact", ["chassis_id"], ""
    if a["management_mac"] and a["management_mac"] == b["management_mac"]:
        return "exact", ["management_mac"], ""
    site_match = a["site"] and a["site"] == b["site"]
    vendor_discovery = "snmp" in sources and bool(sources & VENDOR_SOURCES)
    if hostname_match and (site_match or (
            vendor_discovery and _site_alias(
                left.get("site_id"), right.get("site_id")))):
        evidence = ["hostname"] + (["site"] if site_match else ["collector_pair"])
        return "high", evidence, ""
    if ip_match and (site_match or (
            vendor_discovery and _site_alias(
                left.get("site_id"), right.get("site_id")))):
        return "medium", ["management_ip"] + (["site"] if site_match else ["collector_pair"]), ""
    if hostname_match or ip_match:
        return "low", ["hostname" if hostname_match else "management_ip"], "site identity is incompatible"
    return "unmerged", [], ""


def _conflict(field, values, sources, severity, explanation):
    return {"field": field, "values": sorted(str(value) for value in values),
        "sources": sorted(sources), "severity": severity, "explanation": explanation,
        "resolution_status": "selected_by_policy"}


class FusionEngine:
    def __init__(self, freshness_seconds=STATUS_FRESHNESS_SECONDS):
        self.freshness_seconds = int(freshness_seconds)

    @staticmethod
    def _observations(results):
        chosen = {}
        for result in results:
            for raw in result.assets:
                value = dict(raw); source = _source(value)
                key = (source, str(value.get("source_asset_id") or value.get("asset_id") or ""))
                candidate = (result.priority, result.name, value)
                if key not in chosen or candidate[:2] > chosen[key][:2]: chosen[key] = candidate
        observations = []
        for (_, _), (priority, authority, value) in sorted(chosen.items()):
            value["_authority"] = authority; value["_priority"] = priority
            observations.append(value)
        return observations

    def fuse(self, results):
        records = self._observations(results); count = len(records)
        parent = list(range(count)); edges = []; low = []
        def find(value):
            while parent[value] != value:
                parent[value] = parent[parent[value]]; value = parent[value]
            return value
        def union(left, right):
            left = find(left); right = find(right)
            if left == right: return True
            left_members = [index for index in range(count) if find(index) == left]
            right_members = [index for index in range(count) if find(index) == right]
            left_serials = {normalized(records[index])["serial"] for index in left_members
                            if normalized(records[index])["serial"]}
            right_serials = {normalized(records[index])["serial"] for index in right_members
                             if normalized(records[index])["serial"]}
            if left_serials and right_serials and left_serials != right_serials: return False
            if any(not compatible_types(normalized(records[a])["device_type"],
                                        normalized(records[b])["device_type"])
                   for a in left_members for b in right_members): return False
            parent[max(left, right)] = min(left, right); return True
        for left in range(count):
            for right in range(left + 1, count):
                confidence, matched, reason = classify_match(records[left], records[right])
                if confidence in {"exact", "high", "medium"}:
                    if union(left, right): edges.append((left, right, confidence, matched))
                    else: low.append({"left": records[left], "right": records[right],
                                      "matched_on": matched, "reason": "cluster identity conflict"})
                elif confidence == "low":
                    low.append({"left": records[left], "right": records[right],
                                "matched_on": matched, "reason": reason})
        groups = {}
        for index in range(count): groups.setdefault(find(index), []).append(index)
        assets = []; statistics = {"source_records": count, "canonical_assets": len(groups),
            "records_fused": count - len(groups), "exact_matches": 0, "high_confidence_matches": 0,
            "medium_confidence_matches": 0, "low_confidence_candidates": len(low), "conflicts": 0}
        for indices in groups.values():
            group = [records[index] for index in indices]
            group_edges = [edge for edge in edges if edge[0] in indices and edge[1] in indices]
            confidence = max((edge[2] for edge in group_edges), key=lambda value: CONFIDENCE_RANK[value],
                             default="unmerged")
            if confidence == "exact": statistics["exact_matches"] += 1
            elif confidence == "high": statistics["high_confidence_matches"] += 1
            elif confidence == "medium": statistics["medium_confidence_matches"] += 1
            asset = self._fuse_group(group, confidence,
                sorted({item for edge in group_edges for item in edge[3]}))
            statistics["conflicts"] += len(asset["merge"]["conflicts"]); assets.append(asset)
        assets.sort(key=lambda value: value["canonical_id"])
        return assets, statistics, low

    def _select(self, records, field):
        values = [record for record in records if record.get(field) not in (None, "", [])]
        if not values: return None, None
        values.sort(key=lambda record: (-source_priority(_source(record), record.get("_authority")),
            _source(record), str(record.get(field))))
        record = values[0]
        return record[field], {"source": record.get("_authority") or _source(record),
                               "value": record[field]}

    def _status(self, records):
        candidates = [record for record in records if record.get("online") is not None]
        if not candidates: return "unknown", None, []
        newest = max((_observed(record) for record in candidates if _observed(record)), default=None)
        if newest:
            fresh = [record for record in candidates if _observed(record) and
                     abs((newest - _observed(record)).total_seconds()) <= self.freshness_seconds]
        else: fresh = candidates
        fresh.sort(key=lambda record: (-(1 if _source(record) in VENDOR_SOURCES else 0),
            -source_priority(_source(record), record.get("_authority")), _source(record)))
        selected = fresh[0]; states = {bool(record["online"]) for record in fresh}
        conflicts = []
        if len(states) > 1:
            conflicts.append(_conflict("status", ["online" if value else "offline" for value in states],
                {_source(record) for record in fresh}, "High",
                f"Fresh explicit states disagree within {self.freshness_seconds} seconds."))
        state = "online" if selected["online"] else "offline"
        return state, {"source": _source(selected), "value": state}, conflicts

    def _fuse_group(self, records, confidence, matched_on):
        result = {}; provenance = {}; conflicts = []
        for field in FUSION_FIELDS:
            result[field], provenance[field] = self._select(records, field)
            if provenance[field] is None: provenance.pop(field, None)
        result["canonical_id"] = canonical_id(records)
        result["asset_id"] = result["canonical_id"]
        result["mac_addresses"] = sorted({value for record in records for value in
            (normalize_mac(record.get("mac_address")), normalize_mac(record.get("management_mac")),
             normalize_mac(record.get("chassis_mac"))) if value})
        result["sources"] = sorted({_source(record) for record in records})
        seen = set(); source_records = []
        for record in sorted(records, key=lambda value: (_source(value), str(value.get("source_asset_id") or value.get("asset_id") or ""))):
            key = (_source(record), str(record.get("source_asset_id") or record.get("asset_id") or ""))
            if key in seen: continue
            seen.add(key); source_records.append({"source": key[0], "source_asset_id": key[1],
                "observed_at": record.get("source_last_seen_at") or record.get("last_seen_at"),
                "site_value": record.get("_site_source_value")})
        result["source_records"] = source_records
        status, status_provenance, status_conflicts = self._status(records)
        result["status"] = status; result["online"] = True if status == "online" else False if status == "offline" else None
        if status_provenance: provenance["status"] = status_provenance
        conflicts.extend(status_conflicts)
        checks = (("serial_number", normalize_serial, "Critical"), ("management_ip", normalize_ip, "Medium"),
                  ("deployment_id", lambda value: str(value or "").casefold() or None, "Critical"),
                  ("customer_id", lambda value: str(value or "").casefold() or None, "Critical"),
                  ("site_id", lambda value: str(value or "").lower() or None, "High"),
                  ("site", normalize_site, "Medium"), ("device_type", normalize_device_type, "High"),
                  ("vendor", lambda value: str(value or "").lower() or None, "Low"),
                  ("model", lambda value: str(value or "").lower() or None, "Low"))
        for field, normalizer, severity in checks:
            values = {normalizer(record.get(field)) for record in records if normalizer(record.get(field))}
            if len(values) > 1:
                conflicts.append(_conflict(field, values, {_source(record) for record in records}, severity,
                    f"Non-empty {field.replace('_', ' ')} values disagree; precedence selected the canonical value."))
        result["field_provenance"] = provenance
        result["merge"] = {"confidence": confidence, "matched_on": matched_on,
                           "conflicts": sorted(conflicts, key=lambda value: (value["field"], value["severity"]))}
        result["collector"] = result["sources"][0] if len(result["sources"]) == 1 else "multi-source"
        result["source"] = result["collector"]
        return {key: value for key, value in result.items() if value is not None}
