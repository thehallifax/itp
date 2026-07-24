"""VMware response-contract parser."""


def parse(value):
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("unsupported VMware fixture/API schema version")
    if value.get("provider") != "vmware":
        raise ValueError("VMware response has an invalid provider")
    return value
