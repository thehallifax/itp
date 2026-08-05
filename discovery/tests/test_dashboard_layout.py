import copy

import pytest

from analysis.dashboards.layout import (
    PanelState,
    apply_managed_presentation,
    compact_information_panel,
    pack_row,
    reflow_classic_dashboard,
)


def panel(identifier, title, x, y, width=6, height=4, kind="stat"):
    return {"id": identifier, "title": title, "type": kind,
            "gridPos": {"x": x, "y": y, "w": width, "h": height}}


def assert_no_overlap(dashboard):
    occupied = set()
    for value in dashboard["panels"]:
        grid = value["gridPos"]
        cells = {(x, y) for x in range(grid["x"], grid["x"] + grid["w"])
                 for y in range(grid["y"], grid["y"] + grid["h"])}
        assert not occupied & cells
        occupied.update(cells)


def test_pack_row_expands_neighbours_and_preserves_ids():
    values = [panel(10, "One", 0, 5), panel(20, "Two", 12, 5)]
    assert pack_row(values, y=3, height=5) == 8
    assert [value["id"] for value in values] == [10, 20]
    assert [value["gridPos"] for value in values] == [
        {"x": 0, "y": 3, "w": 12, "h": 5},
        {"x": 12, "y": 3, "w": 12, "h": 5},
    ]


@pytest.mark.parametrize(("scenario", "omitted", "state"), [
    ("populated", set(), None),
    ("partially-populated", {3}, PanelState.CONFIGURATION_REQUIRED),
    ("disabled-capability", {2, 3}, PanelState.DISABLED),
    ("first-run", {3}, PanelState.WAITING),
    ("empty-inventory", {2, 3}, PanelState.NOT_COLLECTED),
])
def test_adaptive_layout_scenarios_are_deterministic(
        scenario, omitted, state):
    source = {"uid": f"scenario-{scenario}", "panels": [
        panel(1, "Summary", 0, 0, 24, 1, "row"),
        panel(2, "Left", 0, 1, 8),
        panel(3, "Middle", 8, 1, 8),
        panel(4, "Right", 16, 1, 8),
    ]}
    for value in source["panels"]:
        if value["id"] in omitted:
            value["_itp_omit"] = True
    if state is not None:
        compact_information_panel(source["panels"][-1], state, "Operator action")
    first = apply_managed_presentation(copy.deepcopy(source))
    second = apply_managed_presentation(copy.deepcopy(source))
    assert first == second
    assert [value["id"] for value in first["panels"]] == [
        value for value in (1, 2, 3, 4) if value not in omitted]
    content = [value for value in first["panels"] if value["type"] != "row"]
    assert sum(value["gridPos"]["w"] for value in content) == 24
    assert all(value["fieldConfig"]["defaults"]["noValue"] ==
               PanelState.NOT_COLLECTED.value for value in first["panels"])
    assert_no_overlap(first)


def test_compact_empty_state_is_explicit_and_has_no_placeholder_query():
    value = panel(9, "Exceptions", 0, 8, 24, 10, "table")
    compact_information_panel(
        value, PanelState.HEALTHY, "No current operational findings.")
    assert value["id"] == 9
    assert value["type"] == "text"
    assert value["targets"] == []
    assert value["gridPos"]["h"] == 3
    assert "Healthy - No issues detected" in value["options"]["content"]


def test_reflow_preserves_stable_order_ids_and_classic_schema():
    dashboard = {"panels": [
        panel(1, "A", 0, 0, 8), panel(2, "B", 8, 0, 8),
        {**panel(3, "C", 16, 0, 8), "_itp_omit": True},
        panel(4, "D", 0, 9, 24, 5),
    ]}
    reflow_classic_dashboard(dashboard)
    assert [value["id"] for value in dashboard["panels"]] == [1, 2, 4]
    assert [value["gridPos"]["w"] for value in dashboard["panels"][:2]] == [12, 12]
    assert "layout" not in dashboard and "elements" not in dashboard
    assert_no_overlap(dashboard)


def test_managed_presentation_normalizes_legacy_empty_labels():
    dashboard = {"panels": [panel(1, "Legacy", 0, 0, 24, 4)]}
    dashboard["panels"][0]["fieldConfig"] = {
        "defaults": {"noValue": "No Matching Records"}}
    apply_managed_presentation(dashboard)
    assert dashboard["panels"][0]["fieldConfig"]["defaults"]["noValue"] == (
        PanelState.NOT_COLLECTED.value)


def test_query_panel_description_preserves_measurement_diagnostics():
    dashboard = {"panels": [panel(1, "Devices", 0, 0, 24, 4)]}
    dashboard["panels"][0]["targets"] = [{
        "rawSql": "SELECT hostname FROM infrastructure_device"}]
    apply_managed_presentation(dashboard)
    description = dashboard["panels"][0]["description"]
    assert "`infrastructure_device`" in description
    assert "missing column" in description
    assert "Query failures remain visible" in description
