"""Telegraf SNMP configuration generation."""
from pathlib import Path

from collectors.writer import atomic_remove, atomic_write
from ._implementation import group_devices, render_group, toml_string

GENERATED_FILES = {
    "printer": "discovered-printers.conf",
    "network-switch": "discovered-switches.conf",
    "wireless-access-point": "discovered-access-points.conf",
    "ups": "discovered-ups.conf",
    "synology": "discovered-synology.conf",
}

IDENTITY_PROCESSOR = '''
[[processors.starlark]]
  source = """
def apply(metric):
    address = metric.tags.get("device_ip")
    if address:
        metric.tags["device_id"] = "snmp:" + address
    return metric
"""
'''


def generate_configs(inventory, communities, output_dir):
    rendered = {platform: [] for platform in GENERATED_FILES}
    for key, devices in sorted(group_devices(inventory).items()):
        rendered[key[3]].append(render_group(key, devices, communities))
    for platform, filename in GENERATED_FILES.items():
        path = Path(output_dir) / filename
        content = "\n".join(rendered[platform])
        if content:
            content += IDENTITY_PROCESSOR
        if platform == "wireless-access-point" and not content:
            atomic_remove(path)
        else:
            atomic_write(path, content)


__all__ = ["GENERATED_FILES", "generate_configs", "group_devices", "render_group", "toml_string"]
