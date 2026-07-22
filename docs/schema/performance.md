# performance

Purpose: generic device resource utilization. Tags: common device tags. Fields:
`cpu_percent`, `memory_used_percent` (percent, 0–100), `uptime_seconds` (seconds), optional
`temperature_celsius` (°C), `disk_used_bytes`, `disk_total_bytes` (bytes). Example:
`performance,collector=fortigate,device_id=fortigate:FG1 cpu_percent=12,memory_used_percent=48`.
Collectors: Mist, FortiGate API, FortiGate SNMP transitional adapter.
