# availability

Purpose: observation-level reachability. Tags: common device tags (`collector`, `customer`,
`site`, `device_id`, `hostname`, `vendor`, `platform`, `device_role`). Fields: `available`
(boolean), optional `latency_ms` (milliseconds), `status` and `reason` (strings). Example:
`availability,collector=fortigate,device_id=fortigate:FG1 available=true`. Collectors: Mist,
FortiGate API, SNMP/Telegraf transitional adapter.
