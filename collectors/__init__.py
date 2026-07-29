"""Collector framework for monitoring data sources."""
from .base import BaseCollector
from .inventory import InventoryAsset, InventoryEngine, InventoryManager
from .registry import CollectorRegistry
from .connector_registry import ConnectorMetadata, ConnectorMetadataRegistry

__all__ = ["BaseCollector", "ConnectorMetadata", "ConnectorMetadataRegistry",
           "CollectorRegistry", "InventoryAsset", "InventoryEngine",
           "InventoryManager", "SNMPCollector", "MistCollector", "FortiGateCollector",
           "PaloAltoCollector", "ArubaCentralCollector"]


def __getattr__(name):
    """Load runtime collectors only when callers explicitly request them."""
    modules = {
        "SNMPCollector": (".snmp", "SNMPCollector"),
        "MistCollector": (".mist", "MistCollector"),
        "FortiGateCollector": (".fortigate", "FortiGateCollector"),
        "PaloAltoCollector": (".paloalto", "PaloAltoCollector"),
        "ArubaCentralCollector": (".aruba", "ArubaCentralCollector"),
    }
    if name not in modules:
        raise AttributeError(name)
    import importlib
    module, attribute = modules[name]
    return getattr(importlib.import_module(module, __name__), attribute)
