"""Durable file output and future InfluxDB writer boundary."""
import math
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from telemetry import DeploymentMetadata, normalize_point
from .configuration import influx_write_debug_enabled


LOG = logging.getLogger("collector.influx_writer")
_SENSITIVE_NAME = re.compile(
    r"(?i)(authorization|api[_-]?key|token|password|secret|cookie)")
_FIELD_NAME_PATTERNS = (
    re.compile(r"(?i)field(?: key| name)?\s*['\"`]([^'\"`]+)"),
    re.compile(r"(?i)field(?: key| name)?\s*[:=]\s*([A-Za-z0-9_.-]+)"),
    re.compile(r"(?i)column\s*['\"`]([^'\"`]+)"),
)
_TAG_NAME_PATTERNS = (
    re.compile(r"(?i)tag(?: key| name)?\s*['\"`]([^'\"`]+)"),
    re.compile(r"(?i)tag(?: key| name)?\s*[:=]\s*([A-Za-z0-9_.-]+)"),
)


class InfluxWriteError(RuntimeError):
    """Safe, structured failure at the canonical telemetry write boundary."""

    def __init__(self, message, *, category="write_failed", http_status=None,
                 response=None, measurements=(), failing_line=None,
                 offending_field=None, offending_tag=None,
                 field_type_conflict=None, invalid_value=None,
                 influx_error=None):
        super().__init__(message)
        self.category = category
        self.stage = "write"
        self.http_status = http_status
        self.response = response
        self.measurements = tuple(measurements)
        self.failing_line = failing_line
        self.offending_field = offending_field
        self.offending_tag = offending_tag
        self.field_type_conflict = field_type_conflict
        self.invalid_value = invalid_value
        self.influx_error = influx_error

    def diagnostic_payload(self):
        value = {"category": self.category, "stage": self.stage,
                 "message": str(self)}
        if self.http_status is not None:
            value["http_status"] = self.http_status
        for key, item in (
                ("response", self.response),
                ("measurements", list(self.measurements)),
                ("failing_line", self.failing_line),
                ("offending_field", self.offending_field),
                ("offending_tag", self.offending_tag),
                ("field_type_conflict", self.field_type_conflict),
                ("invalid_value", self.invalid_value),
                ("influx_error", self.influx_error)):
            if item not in (None, "", [], ()):
                value[key] = item
        return value


def _first_match(patterns, text):
    for pattern in patterns:
        matched = pattern.search(text)
        if matched:
            return matched.group(1).strip()
    return None


def _safe_text(value, secrets=(), limit=4096):
    text = " ".join(str(value or "").replace("\x00", " ").split())
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "[REDACTED]")
    text = re.sub(
        r"(?i)(authorization|api[_-]?key|token|password|secret|cookie)"
        r"(\s*[=:]\s*)([^\s,;&]+)",
        r"\1\2[REDACTED]", text)
    return text[:limit]


def _safe_line(value, secrets=()):
    text = str(value or "")
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "[REDACTED]")
    # Line protocol values follow a sensitive tag/field key after '='. Keep
    # the key for diagnosis while suppressing its value.
    text = re.sub(
        r"(?i)((?:authorization|api[_-]?key|token|password|secret|cookie)"
        r"(?:\\?[ ,=]|[^=])*?=)(?:\"(?:\\.|[^\"])*\"|[^, ]+)",
        r"\1[REDACTED]", text)
    return text[:4096]


def _response_detail(response):
    try:
        value = response.json()
        if isinstance(value, dict):
            return json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (ValueError, TypeError, RuntimeError):
        pass
    try:
        return response.text
    except (AttributeError, RuntimeError):
        return ""


def _influx_error(response_text):
    try:
        value = json.loads(response_text)
    except (TypeError, ValueError):
        return response_text
    if isinstance(value, dict):
        return str(value.get("message") or value.get("error") or response_text)
    return response_text


def _failure_line(lines, response_text):
    matched = re.search(r"(?i)\bline(?: number)?\s*[:= ]\s*(\d+)\b", response_text)
    if matched:
        index = int(matched.group(1)) - 1
        if 0 <= index < len(lines):
            return lines[index]
    return lines[0] if lines else None


def _type_conflict(text):
    if not re.search(r"(?i)(field type|type conflict|already exists.*type)", text):
        return None
    matched = re.search(
        r"(?i)(?:got|received)\s*[=:]?\s*([A-Za-z0-9_]+).*?"
        r"(?:expected|existing|previous(?:ly)?|already exists as)\s*[=:,]?\s*"
        r"([A-Za-z0-9_]+)", text)
    return ({"received": matched.group(1), "existing": matched.group(2)}
            if matched else {"detail": text[:512]})


def atomic_write(path, content, *, mode=None, directory_mode=None):
    """Atomically publish content with an optional explicit permission policy.

    The default remains owner-only through ``mkstemp``. Callers must opt in to
    shared readability for non-secret cross-container artifacts.
    """
    path = Path(path)
    path.parent.mkdir(
        parents=True, exist_ok=True,
        mode=directory_mode if directory_mode is not None else 0o777)
    if directory_mode is not None:
        try:
            path.parent.chmod(directory_mode)
        except OSError:
            # Windows and non-POSIX filesystems may not implement Unix modes.
            pass
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            try:
                os.chmod(temporary, mode)
            except OSError:
                pass
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
                if not math.isfinite(value):
                    raise InfluxWriteError(
                        "InfluxDB line protocol contains a non-finite numeric value",
                        category="invalid_numeric_value",
                        measurements=(point["measurement"],),
                        offending_field=str(key), invalid_value=repr(value))
                encoded = repr(value)
            elif isinstance(value, str) and value:
                encoded = '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n") + '"'
            elif value is None or value == "":
                continue
            else:
                raise InfluxWriteError(
                    "InfluxDB line protocol contains an unsupported field type",
                    category="unsupported_field_type",
                    measurements=(point["measurement"],),
                    offending_field=str(key), invalid_value=type(value).__name__)
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
            raise InfluxWriteError(
                "InfluxDB writer configuration is incomplete",
                category="configuration_incomplete")
        import httpx
        client = self.client or httpx.Client(timeout=self.timeout)
        lines = [line for line in (self.line_protocol(p) for p in points) if line]
        debug = influx_write_debug_enabled()
        if debug:
            measurements = sorted({point["measurement"] for point in points})
            LOG.info("influx.write.debug measurements=%s points=%s",
                     ",".join(measurements), len(lines))
            for index, line in enumerate(lines[:5], 1):
                LOG.info("influx.write.debug line=%s line_protocol=%s", index,
                         _safe_line(line, (self.token,)))
        written = 0
        try:
            for offset in range(0, len(lines), self.batch_size):
                body = "\n".join(lines[offset:offset + self.batch_size])
                for attempt in range(self.retries + 1):
                    try:
                        response = client.post(f"{self.url}/api/v3/write_lp",
                            params={"db": self.database, "precision": "ns"}, content=body,
                            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "text/plain"})
                        if response.status_code < 300:
                            if debug:
                                response_text = _safe_text(
                                    _response_detail(response), (self.token,))
                                LOG.info(
                                    "influx.write.debug http_status=%s response=%s",
                                    response.status_code,
                                    response_text or "<empty>")
                            break
                        response_text = _safe_text(
                            _response_detail(response), (self.token,))
                        influx_error = _safe_text(
                            _influx_error(response_text), (self.token,))
                        if debug:
                            LOG.info(
                                "influx.write.debug http_status=%s response=%s",
                                response.status_code, response_text or "<empty>")
                        if response.status_code not in (429, 500, 502, 503, 504) or attempt == self.retries:
                            category = (
                                "influx_authentication_failed"
                                if response.status_code in {401, 403} else
                                "influx_database_missing"
                                if response.status_code == 404 else "write_failed")
                            batch_lines = lines[offset:offset + self.batch_size]
                            failing_line = _safe_line(
                                _failure_line(batch_lines, response_text),
                                (self.token,))
                            measurements = sorted({
                                line.split(",", 1)[0].split(" ", 1)[0]
                                for line in batch_lines})
                            field = _first_match(
                                _FIELD_NAME_PATTERNS, response_text)
                            tag = _first_match(_TAG_NAME_PATTERNS, response_text)
                            detail = (
                                f"InfluxDB rejected telemetry (HTTP {response.status_code})"
                                + (f": {influx_error}" if influx_error else ""))
                            raise InfluxWriteError(
                                detail,
                                category=category,
                                http_status=response.status_code,
                                response=response_text,
                                influx_error=influx_error,
                                measurements=measurements,
                                failing_line=failing_line,
                                offending_field=field,
                                offending_tag=tag,
                                field_type_conflict=_type_conflict(response_text))
                    except httpx.TransportError as exc:
                        if attempt == self.retries:
                            raise InfluxWriteError(
                                "InfluxDB write transport failure") from exc
                    time.sleep(min(2 ** attempt, 4))
                written += len(lines[offset:offset + self.batch_size])
        finally:
            if self.client is None: client.close()
        return written
