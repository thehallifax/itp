"""Shared deterministic presentation helpers for classic Grafana dashboards."""
from __future__ import annotations

import copy
from enum import Enum

from .contracts import query_measurements

GRID_WIDTH = 24


class PanelState(str, Enum):
    """Vendor-neutral operator states used by every managed dashboard."""

    HEALTHY = "Healthy - No issues detected"
    WAITING = "Awaiting first collection"
    DISABLED = "Capability not enabled"
    CONFIGURATION_REQUIRED = "Additional telemetry required"
    UNSUPPORTED = "Not available for this platform"
    NOT_COLLECTED = "Not Yet Collected"
    UNAVAILABLE = "Collection unavailable"


def pack_row(panels, *, y, height=None, width=GRID_WIDTH):
    """Fill one Grafana row deterministically while preserving panel IDs."""
    values = list(panels)
    if not values:
        return y
    base, remainder = divmod(width, len(values))
    x = 0
    row_height = max(
        int((value.get("gridPos") or {}).get("h") or 1)
        for value in values) if height is None else int(height)
    for index, panel in enumerate(values):
        panel_width = base + (1 if index < remainder else 0)
        panel_height = (int((panel.get("gridPos") or {}).get("h") or 1)
                        if height is None else row_height)
        panel["gridPos"] = {
            "x": x, "y": int(y), "w": panel_width, "h": panel_height}
        x += panel_width
    return int(y) + row_height


def compact_information_panel(panel, state, detail=""):
    """Turn a known non-data panel into a compact, explicit operator card."""
    label = state.value if isinstance(state, PanelState) else str(state)
    panel["type"] = "text"
    panel.pop("datasource", None)
    panel["targets"] = []
    panel["transformations"] = []
    panel["options"] = {
        "mode": "markdown",
        "content": f"### {label}" + (f"\n\n{detail}" if detail else ""),
    }
    grid = panel.setdefault("gridPos", {})
    grid["h"] = min(int(grid.get("h") or 3), 3)
    return panel


def reflow_classic_dashboard(dashboard):
    """Remove marked panels and close gaps in every classic dashboard row."""
    panels = [copy.deepcopy(value) for value in dashboard.get("panels", [])
              if value.get("_itp_omit") is not True]
    for panel in panels:
        panel.pop("_itp_omit", None)
    ordered = sorted(panels, key=lambda value: (
        int((value.get("gridPos") or {}).get("y") or 0),
        int((value.get("gridPos") or {}).get("x") or 0),
        int(value.get("id") or 0)))
    result = []
    y = 0
    index = 0
    while index < len(ordered):
        source_y = int((ordered[index].get("gridPos") or {}).get("y") or 0)
        group = []
        while index < len(ordered) and int(
                (ordered[index].get("gridPos") or {}).get("y") or 0) == source_y:
            group.append(ordered[index]); index += 1
        rows = [value for value in group if value.get("type") == "row"]
        content = [value for value in group if value.get("type") != "row"]
        for row in rows:
            row["gridPos"] = {"x": 0, "y": y, "w": GRID_WIDTH,
                              "h": int((row.get("gridPos") or {}).get("h") or 1)}
            y += row["gridPos"]["h"]
            result.append(row)
        if content:
            y = pack_row(content, y=y)
            result.extend(content)
    dashboard["panels"] = result
    return dashboard


def apply_managed_presentation(dashboard):
    """Apply the shared presentation contract to any managed dashboard."""
    aliases = {
        "No Matching Records": PanelState.NOT_COLLECTED.value,
        "No telemetry collected": PanelState.NOT_COLLECTED.value,
        "Waiting for telemetry": PanelState.WAITING.value,
        "Awaiting First Collection": PanelState.WAITING.value,
        "Traffic unavailable": PanelState.CONFIGURATION_REQUIRED.value,
    }
    for panel in dashboard.get("panels", []):
        defaults = panel.setdefault("fieldConfig", {}).setdefault("defaults", {})
        current = defaults.get("noValue")
        defaults["noValue"] = aliases.get(
            current, current or PanelState.NOT_COLLECTED.value)
        measurements = sorted({measurement
                               for target in panel.get("targets", [])
                               for measurement in query_measurements(
                                   target.get("rawSql") or "")})
        if measurements:
            evidence = (
                "Telemetry measurement"
                + ("s" if len(measurements) != 1 else "")
                + ": " + ", ".join(f"`{value}`" for value in measurements)
                + ". Query failures remain visible; inspect the panel query "
                  "for the missing column and validate the collector contract.")
            existing = str(panel.get("description") or "").strip()
            if evidence not in existing:
                panel["description"] = (
                    f"{existing}\n\n{evidence}".strip())
    return reflow_classic_dashboard(dashboard)
