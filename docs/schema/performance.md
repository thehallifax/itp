# performance

Purpose: generic device resource utilization. Tags: common device tags. Fields:
`cpu_percent`, `memory_used_percent` (percent, 0–100), `uptime_seconds` (seconds), optional
`temperature_celsius` (°C), `disk_used_bytes`, `disk_total_bytes` (bytes). Example:
`performance,collector=fortigate,device_id=fortigate:FG1 cpu_percent=12,memory_used_percent=48`.
Collectors: Mist, FortiGate API, FortiGate SNMP transitional adapter.

Palo Alto fields use percentages from 0–100:
`management_cpu_percent`, `dataplane_cpu_percent`, `memory_used_percent`,
`packet_buffer_used_percent`, and `session_utilisation_percent`.

Session fields are `sessions_active`, `sessions_max`, `sessions_tcp`, and
`sessions_udp`. Utilisation is emitted only when both active and maximum
session counts are valid.
