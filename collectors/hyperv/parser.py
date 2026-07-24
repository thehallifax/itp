"""Versioned Hyper-V PowerShell JSON contract parser."""


def parse(value):
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("unsupported Hyper-V PowerShell schema version")
    if value.get("provider") != "hyperv":
        raise ValueError("Hyper-V response has an invalid provider")
    return value
