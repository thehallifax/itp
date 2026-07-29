"""Read-only VMware vSphere REST client boundary."""
import httpx


class VMwareClient:
    def __init__(self, endpoint, username, password, *, verify=True, timeout=20):
        self.endpoint = endpoint.rstrip("/")
        self.username = username
        self.password = password
        self.client = httpx.Client(verify=verify, timeout=timeout)

    def collect(self):
        response = self.client.post(self.endpoint + "/api/session",
            auth=(self.username, self.password))
        response.raise_for_status()
        session = response.json()
        token = session if isinstance(session, str) else session.get("value")
        headers = {"vmware-api-session-id": token}
        diagnostics = []

        def get(path):
            try:
                value = self.client.get(self.endpoint + path, headers=headers)
                value.raise_for_status()
                payload = value.json()
                return payload.get("value", payload) if isinstance(payload, dict) else payload
            except httpx.HTTPError as exc:
                diagnostics.append({"section": path, "category": "partial_endpoint_failure",
                                    "message": type(exc).__name__})
                return []

        try:
            version = get("/api/appliance/system/version")
            clusters = get("/api/vcenter/cluster")
            hosts = get("/api/vcenter/host")
            vms = get("/api/vcenter/vm")
            datastores = get("/api/vcenter/datastore")
            networks = get("/api/vcenter/network")
            return {
                "schema_version": 1, "provider": "vmware",
                "manager": {"id": self.endpoint, "name": "vCenter",
                    "version": version.get("version") if isinstance(version, dict) else None,
                    "build": version.get("build") if isinstance(version, dict) else None,
                    "reachable": True},
                "clusters": [{"id": value.get("cluster"), "name": value.get("name"),
                              "health": "unknown"} for value in clusters],
                "hosts": [{"id": value.get("host"), "name": value.get("name"),
                    "connection_state": str(value.get("connection_state", "unknown")).lower(),
                    "state": value.get("power_state"), "health": "unknown"}
                    for value in hosts],
                "vms": [{"id": value.get("vm"), "name": value.get("name"),
                    "state": value.get("power_state"),
                    "vcpu": value.get("cpu_count"),
                    "memory_bytes": int(value["memory_size_MiB"] * 1024 * 1024)
                        if value.get("memory_size_MiB") is not None else None}
                    for value in vms],
                "containers": [],
                "storage": [{"id": value.get("datastore"), "name": value.get("name"),
                    "storage_type": value.get("type"), "accessible": True}
                    for value in datastores],
                "networks": [{"id": value.get("network"), "name": value.get("name"),
                              "network_type": value.get("type")} for value in networks],
                "snapshots": [], "diagnostics": diagnostics,
            }
        finally:
            self.client.delete(self.endpoint + "/api/session", headers=headers)
