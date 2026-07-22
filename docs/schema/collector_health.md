# collector_health

Purpose: one operational result per collector run. Tags: `collector`, `customer`, `site`;
optional `diagnostic_category`. Fields: `success`, `partial` (boolean), `duration_ms`
(milliseconds), `api_requests`, `retry_count`, `error_count`, `devices_returned`,
`points_written` (counts). Example:
`collector_health,collector=fortigate,customer=acme,site=hq success=true,partial=false,duration_ms=120i`.
Collectors: Mist and FortiGate API; other collectors adopt this contract incrementally.
