"""Durable file output and future InfluxDB writer boundary."""
import math
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from telemetry import DeploymentMetadata, normalize_point


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_remove(path):
    path = Path(path)
    try:
        path.unlink()
    except FileNotFoundError:
        return
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


class InfluxWriter:
    """Batched InfluxDB 3 line-protocol writer for native collectors."""

    def __init__(self, delegate=None, *, url=None, token=None, database=None,
                 deployment_id="", customer_id="", site_id="",
                 customer_name="", site_name="",
                 batch_size=500, timeout=20, retries=2,
                 client=None, accept_legacy_health=True):
        self.delegate = delegate
        self.url = self._normalize_url(url or "")
        self.token = token
        self.database = database
        self.deployment_id = str(deployment_id or "").strip()
        self.customer_id = str(customer_id or "").strip()
        self.site_id = str(site_id or "").strip()
        self.customer_name = str(customer_name or "").strip()
        self.site_name = str(site_name or "").strip()
        self.metadata = DeploymentMetadata(
            self.deployment_id, self.customer_id, self.site_id,
            self.customer_name, self.site_name)
        self.last_diagnostics = ()
        self.batch_size = batch_size
        self.timeout = timeout
        self.retries = retries
        self.client = client
        self.accept_legacy_health = bool(accept_legacy_health)

    @classmethod
    def from_config(cls, config, **kwargs):
        settings = config.get("writer") or {}
        metadata = DeploymentMetadata.from_config(config)
        return cls(
            url=settings.get("url"),
            token=settings.get("token"),
            database=settings.get("database"),
            deployment_id=metadata.deployment_id,
            customer_id=metadata.customer_id,
            site_id=metadata.site_id,
            customer_name=metadata.customer_name,
            site_name=metadata.site_name,
            accept_legacy_health=False,
            **kwargs)

    @staticmethod
    def _normalize_url(value):
        if not value: return ""
        value = value if "://" in value else f"http://{value}"
        parsed = urlsplit(value)
        netloc = parsed.netloc if parsed.port is not None else f"{parsed.hostname}:8181"
        return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))

    @staticmethod
    def _escape(value, chars):
        return re.sub(f"([{re.escape(chars)}])", r"\\\1", str(value))

    @classmethod
    def line_protocol(cls, point):
        measurement = cls._escape(point["measurement"], " ,\\")
        tag_escape_characters = " ,=\\"
        tags = "".join(f",{cls._escape(k, tag_escape_characters)}={cls._escape(v, tag_escape_characters)}"
                       for k, v in sorted(point.get("tags", {}).items()) if v not in (None, ""))
        fields = []
        for key, value in sorted(point["fields"].items()):
            name = cls._escape(key, " ,=\\")
            if isinstance(value, bool): encoded = "true" if value else "false"
            elif isinstance(value, int): encoded = f"{value}i"
            elif isinstance(value, float):
                if not math.isfinite(value): continue
                encoded = repr(value)
            elif isinstance(value, str) and value:
                encoded = '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n") + '"'
            else: continue
            fields.append(f"{name}={encoded}")
        if not fields:
            return None
        timestamp = f" {int(point['timestamp'])}" if point.get("timestamp") is not None else ""
        return f"{measurement}{tags} {','.join(fields)}{timestamp}"

    def write(self, points):
        # Preserve the original wrapper API for delegates that accept an
        # opaque payload rather than canonical point dictionaries.
        if self.delegate and (
                not isinstance(points, (list, tuple))
                or any(not isinstance(point, dict) for point in points)):
            return self.delegate(points)
        points = [
            point for point in points
            if self.accept_legacy_health
            or point.get("measurement") != "collector_health"
            or (point.get("tags") or {}).get("health_owner") == "framework"]
        points = [
            normalize_point(point, self.metadata, index)
            for index, point in enumerate(points, 1)]
        if self.delegate:
            return self.delegate(points)
        if not self.url or not self.token or not self.database:
            raise ValueError("INFLUXDB_HOST, INFLUXDB_TOKEN and INFLUXDB_BUCKET are required")
        import httpx
        client = self.client or httpx.Client(timeout=self.timeout)
        lines = [line for line in (self.line_protocol(p) for p in points) if line]
        written = 0
        try:
            for offset in range(0, len(lines), self.batch_size):
                body = "\n".join(lines[offset:offset + self.batch_size])
                for attempt in range(self.retries + 1):
                    try:
                        response = client.post(f"{self.url}/api/v3/write_lp",
                            params={"db": self.database, "precision": "ns"}, content=body,
                            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "text/plain"})
                        if response.status_code < 300: break
                        if response.status_code not in (429, 500, 502, 503, 504) or attempt == self.retries:
                            raise RuntimeError(f"InfluxDB write failed with HTTP {response.status_code}")
                    except httpx.TransportError as exc:
                        if attempt == self.retries: raise RuntimeError("InfluxDB write transport failure") from exc
                    time.sleep(min(2 ** attempt, 4))
                written += len(lines[offset:offset + self.batch_size])
        finally:
            if self.client is None: client.close()
        return written
