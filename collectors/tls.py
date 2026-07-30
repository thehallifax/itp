"""Shared HTTPS trust helpers for connector clients."""
from __future__ import annotations

import ssl
from pathlib import Path


def connector_tls_context(verify_tls=True, ca_bundle=None):
    """Return normal public trust extended by a deployment CA bundle."""
    if not verify_tls:
        return False
    context = ssl.create_default_context()
    bundle = str(ca_bundle or "").strip()
    if bundle:
        path = Path(bundle)
        if not path.is_file():
            raise ValueError(f"deployment CA bundle does not exist: {path}")
        context.load_verify_locations(cafile=str(path))
    return context


def deployment_ca_bundle(config):
    value = config.get("tls") or {}
    return str(value.get("ca_bundle") or "").strip() or None
