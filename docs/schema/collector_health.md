# collector_health

Purpose: one operational result per collector run. Tags: `collector`, `customer`, `site`;
optional `diagnostic_category`. Fields: `success`, `partial` (boolean), `duration_ms`
(milliseconds), `api_requests`, `retry_count`, `error_count`, `devices_returned`,
`points_written` (counts). Example:
`collector_health,collector=fortigate,customer=acme,site=hq success=true,partial=false,duration_ms=120i`.
Collectors: Mist, FortiGate API, Palo Alto, and PaperCut MF; other collectors
adopt this contract incrementally.

Palo Alto adds `api_duration_ms_total` and `api_duration_ms_max` to the
existing `api_requests`, `retry_count`, `partial`, `error_count`,
`duration_ms`, and `points_written` fields. Command names and safe failure
categories remain internal and raw URLs, XML, and error bodies are never tags.
