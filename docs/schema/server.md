# server

Purpose: future server identity, operating-system, and resource telemetry. Tags: common
device tags plus optional `os_family`, `environment`, `service_role`. Fields:
`online` (boolean), `cpu_percent`, `memory_used_percent`, `disk_used_percent` (percent),
`load_1m` (ratio), `uptime_seconds` (seconds). Example:
`server,collector=agent,device_id=server:1 online=true,cpu_percent=8`. Collectors: none in
schema version 1; this contract reserves normalized output for a future server collector.
