"""Proxmox REST response-contract parser."""


def parse(value):
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("unsupported Proxmox fixture/API schema version")
    if value.get("provider") != "proxmox":
        raise ValueError("Proxmox response has an invalid provider")
    return value
