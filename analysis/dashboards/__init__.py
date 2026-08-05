"""Dashboard generation and shared presentation helpers."""
from .contracts import (
    MeasurementContract,
    QueryContractError,
    contracts_from_points,
    dashboard_queries,
    query_measurements,
    query_outcome_state,
    validate_dashboard_contract,
)
from .layout import (
    PanelState,
    apply_managed_presentation,
    compact_information_panel,
    pack_row,
    reflow_classic_dashboard,
)

__all__ = [
    "FOLDERS",
    "DashboardPackRegistry",
    "DashboardRegistry",
    "MeasurementContract",
    "PanelState",
    "QueryContractError",
    "apply_managed_presentation",
    "compact_information_panel",
    "contracts_from_points",
    "dashboard_queries",
    "pack_row",
    "query_measurements",
    "query_outcome_state",
    "reflow_classic_dashboard",
    "validate_dashboard_contract",
]


def __getattr__(name):
    """Load the registry lazily to avoid renderer/registry import cycles."""
    if name in {"DashboardPackRegistry", "DashboardRegistry", "FOLDERS"}:
        from .registry import FOLDERS, DashboardPackRegistry, DashboardRegistry
        return {"DashboardPackRegistry": DashboardPackRegistry,
                "DashboardRegistry": DashboardRegistry,
                "FOLDERS": FOLDERS}[name]
    raise AttributeError(name)
