"""Read-only Proxmox VE REST client."""
import httpx


class ProxmoxClient:
    def __init__(self, endpoint, token_id, token_secret, *, verify=True, timeout=20):
        self.endpoint = endpoint.rstrip("/")
        self.client = httpx.Client(verify=verify, timeout=timeout,
            headers={"Authorization": f"PVEAPIToken={token_id}={token_secret}"})

    def get(self, path):
        response = self.client.get(self.endpoint + "/api2/json" + path)
        response.raise_for_status()
        return response.json().get("data")

    def collect(self):
        version = self.get("/version")
        status = self.get("/cluster/status") or []
        resources = self.get("/cluster/resources") or []
        nodes = [value for value in resources if value.get("type") == "node"]
        qemu = [value for value in resources if value.get("type") == "qemu"]
        lxc = [value for value in resources if value.get("type") == "lxc"]
        storage = [value for value in resources if value.get("type") == "storage"]
        cluster = next((value for value in status if value.get("type") == "cluster"), None)
        snapshots, diagnostics = [], []
        for workload, kind in ([(value, "qemu") for value in qemu]
                               + [(value, "lxc") for value in lxc]):
            try:
                values = self.get(
                    f"/nodes/{workload['node']}/{kind}/{workload['vmid']}/snapshot") or []
                for value in values:
                    if value.get("name") == "current":
                        continue
                    snapshots.append({"id": f"{workload['vmid']}:{value.get('name')}",
                        "workload_id": f"{kind}-{workload['vmid']}",
                        "name": value.get("name"), "created_at": None,
                        "snapshot_type": "snapshot"})
            except httpx.HTTPError as exc:
                diagnostics.append({"section": f"{kind}_snapshots",
                    "category": "partial_endpoint_failure",
                    "message": type(exc).__name__})
        return {
            "schema_version": 1, "provider": "proxmox",
            "manager": {"id": cluster.get("id", "standalone") if cluster else "standalone",
                "name": cluster.get("name", "Proxmox VE") if cluster else "Proxmox VE",
                "version": version.get("version"), "reachable": True},
            "clusters": [] if not cluster else [{"id": cluster.get("id"),
                "name": cluster.get("name"), "total_host_count": len(nodes),
                "enabled_host_count": sum(value.get("status") == "online" for value in nodes),
                "degraded_host_count": sum(value.get("status") != "online" for value in nodes),
                "health": "healthy" if all(value.get("status") == "online"
                                           for value in nodes) else "warning"}],
            "hosts": [{"id": value.get("node"), "name": value.get("node"),
                "cluster_id": cluster.get("id") if cluster else None,
                "connection_state": "connected" if value.get("status") == "online"
                    else "not_responding", "cpu_total_mhz": None,
                "memory_total_bytes": value.get("maxmem"),
                "memory_used_bytes": value.get("mem"), "health": "unknown"}
                for value in nodes],
            "vms": [{"id": f"qemu-{value['vmid']}", "name": value.get("name"),
                "cluster_id": cluster.get("id") if cluster else None,
                "host_id": value.get("node"), "state": value.get("status"),
                "vcpu": value.get("maxcpu"), "memory_bytes": value.get("maxmem"),
                "uptime_seconds": value.get("uptime")} for value in qemu],
            "containers": [{"id": f"lxc-{value['vmid']}", "name": value.get("name"),
                "cluster_id": cluster.get("id") if cluster else None,
                "host_id": value.get("node"), "state": value.get("status"),
                "vcpu": value.get("maxcpu"), "memory_bytes": value.get("maxmem"),
                "uptime_seconds": value.get("uptime")} for value in lxc],
            "storage": [{"id": value.get("id"), "name": value.get("storage"),
                "host_id": value.get("node"), "scope": "host",
                "capacity_bytes": value.get("maxdisk"), "used_bytes": value.get("disk"),
                "accessible": value.get("status") == "available"} for value in storage],
            "networks": [], "snapshots": snapshots, "diagnostics": diagnostics,
        }
