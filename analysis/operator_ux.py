"""Shared, deterministic operator-experience helpers."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE = re.compile(
    r"(?i)(password|passwd|token|secret|credential|authorization|api[_-]?key|private[_-]?key)")


class SafeRedactor:
    """Redact known values and structurally sensitive fields."""
    def __init__(self, known_secrets=(), *, privacy="standard"):
        self.secrets = tuple(sorted(
            {str(value) for value in known_secrets if len(str(value)) >= 4},
            key=len, reverse=True))
        self.privacy = privacy
        self._pseudonyms = {}

    def pseudonym(self, kind, value):
        key = (kind, str(value))
        if key not in self._pseudonyms:
            digest = hashlib.sha256(f"{kind}:{value}".encode()).hexdigest()[:10]
            self._pseudonyms[key] = f"{kind}-{digest}"
        return self._pseudonyms[key]

    def text(self, value, *, pseudonymise=True):
        text = str(value or "")
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        text = re.sub(
            r"(?i)(authorization|token|password|secret|api[_-]?key)"
            r"(\s*[=:]\s*)([^\s,;&]+)", r"\1\2[REDACTED]", text)
        text = re.sub(r"(?i)(Authorization=)[^&\s]+", r"\1[REDACTED]", text)
        if self.privacy == "high" and pseudonymise:
            text = re.sub(
                r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
                lambda match: self.pseudonym("address", match.group(0)), text)
            text = re.sub(
                r"\b[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){1,}\b",
                lambda match: self.pseudonym("hostname", match.group(0)), text)
        return text

    def url(self, value):
        try:
            parsed = urlsplit(str(value))
        except ValueError:
            return self.text(value)
        if not parsed.scheme:
            return self.text(value)
        host = parsed.hostname or ""
        if self.privacy == "high" and host:
            host = self.pseudonym("hostname", host)
        netloc = host
        if parsed.port:
            netloc += f":{parsed.port}"
        query = [(key, "[REDACTED]" if SENSITIVE.search(key) else self.text(item))
                 for key, item in parse_qsl(parsed.query, keep_blank_values=True)]
        return urlunsplit((parsed.scheme, netloc, parsed.path,
                           urlencode(query), ""))

    def value(self, value, key=""):
        if SENSITIVE.search(str(key)):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {item: self.value(content, item)
                    for item, content in sorted(value.items())}
        if isinstance(value, list):
            return [self.value(item, key) for item in value]
        if isinstance(value, tuple):
            return [self.value(item, key) for item in value]
        result = self.url(value) if isinstance(value, str) and "://" in value \
            else self.text(value, pseudonymise=str(key) not in {
                "command", "included", "path"})
        if self.privacy == "high" and isinstance(result, str):
            kind = next((name for name in (
                "hostname", "address", "customer", "site", "asset")
                if name in str(key).casefold()), None)
            if kind and result and result != "[REDACTED]":
                return self.pseudonym(kind, result)
        return result


@dataclass(frozen=True)
class RecoveryAction:
    id: str
    label: str
    command: str
    destructive: bool = False


def redacted_diff(before, after, redactor):
    keys = sorted(set(before) | set(after))
    return [{"field": key, "before": redactor.value(before.get(key), key),
             "after": redactor.value(after.get(key), key)}
            for key in keys if before.get(key) != after.get(key)]
