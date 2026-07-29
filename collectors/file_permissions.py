"""Portable best-effort permissions for local secret files."""
import os
from pathlib import Path


def restrict_owner_access(path, *, platform=None):
    """Apply owner-only POSIX permissions; retain inherited Windows ACLs."""
    if (platform or os.name) == "posix":
        Path(path).chmod(0o600)
