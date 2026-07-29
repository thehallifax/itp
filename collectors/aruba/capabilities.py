"""Static Aruba Central capability declaration."""
from collectors.capabilities import Capability

CAPABILITIES = (
    Capability(
        "account", "Aruba Central account", "supported",
        services=("Monitoring",), phase="discovery"),
    Capability(
        "groups", "Aruba Central groups", "supported",
        services=("Switching", "Wireless"), phase="discovery"),
    Capability(
        "sites", "Aruba Central sites", "supported",
        services=("Switching", "Wireless"), phase="discovery"),
    Capability(
        "inventory", "Device inventory", "conditional",
        measurements=("device",), services=("Switching", "Wireless"),
        condition="At least one device is available to the Aruba Central API."),
    Capability(
        "access_point_inventory", "Access point inventory", "supported",
        measurements=("device",), services=("Wireless",)),
    Capability(
        "switch_inventory", "Switch inventory", "supported",
        measurements=("device",), services=("Switching",),
        health_impact=False),
    Capability(
        "gateway_inventory", "Gateway inventory", "supported",
        measurements=("device",), services=("Internet",),
        health_impact=False),
    Capability(
        "device_health", "Device health", "conditional",
        measurements=("availability",), fields=("available", "status"),
        services=("Switching", "Wireless"),
        condition="At least one device is available to the Aruba Central API."),
    Capability(
        "firmware", "Device firmware", "conditional",
        measurements=("device",), fields=("firmware",),
        services=("Switching", "Wireless"),
        condition="Discovered devices expose firmware metadata."),
    Capability(
        "alerts", "Aruba Central alerts", "conditional",
        services=("Switching", "Wireless"),
        condition="The API role permits access to the alerts endpoint.",
        health_impact=False),
    Capability(
        "client_counts", "Wireless client counts", "conditional",
        measurements=("wireless",), fields=("clients_connected",),
        services=("Wireless",),
        condition="At least one access point exposes a client count.",
        health_impact=False),
    Capability(
        "collector_diagnostics", "Collector diagnostics", "supported",
        measurements=("collector_health",), services=("Monitoring",)),
)
