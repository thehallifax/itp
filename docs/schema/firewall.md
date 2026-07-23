# firewall

Purpose: vendor-neutral security-appliance telemetry. Tags: common device tags. Fields:
`ha_mode`, `ha_status`, `firmware` (strings), `session_count` (count), `session_rate`
(sessions/second), `cpu_percent`, `memory_used_percent` (percent), `last_seen` (RFC3339).
Example: `firewall,collector=fortigate,device_id=fortigate:FG1 ha_status="primary",session_count=100i`.

Palo Alto also emits `device_certificate_status` and `platform_family`.
Collectors: FortiGate API. Unsupported HA data is explicitly `not_collected`.
