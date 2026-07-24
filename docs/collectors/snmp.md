# SNMP infrastructure connector

The SNMP connector discovers configured networks and generates Telegraf polling
configuration for supported infrastructure. Current canonical evidence covers
network devices and, where configured, wireless, printers, servers, UPS/power,
and environmental devices.

Configuration remains manual. Define networks and exclusions in the discovery
configuration, store community values in the ignored `secrets/snmp.env`, and
never place communities directly in tracked files.

SNMP is vendor-neutral fallback and enrichment. It must not override stronger
native API identity or fabricate unsupported device capabilities.
