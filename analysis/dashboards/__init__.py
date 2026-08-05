"""Dashboard generation and shared presentation helpers."""
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
    "PanelState",
    "apply_managed_presentation",
    "compact_information_panel",
    "pack_row",
    "reflow_classic_dashboard",
]


def __getattr__(name):
    """Load the registry lazily to avoid renderer/registry import cycles."""
    if name in {"DashboardPackRegistry", "DashboardRegistry", "FOLDERS"}:
        from .registry import FOLDERS, DashboardPackRegistry, DashboardRegistry
        return {"DashboardPackRegistry": DashboardPackRegistry,
                "DashboardRegistry": DashboardRegistry,
                "FOLDERS": FOLDERS}[name]
    raise AttributeError(name)
