# collector_health

Purpose: one framework-owned operational result per collector phase and
execution attempt, including skipped and failed runs.

Required tags:

- `collector`
- `deployment_id`, `customer_id`, `site_id`
- `runtime`
- `execution_mode`
- `phase`
- `status`
- `health_owner=framework`

Compatibility tags `customer` and `site` equal their canonical ID fields.
`diagnostic_category` temporarily mirrors status for existing dashboards.

Fields:

- `success`, `partial` (boolean)
- `duration_ms`
- `points_generated`, `points_written`
- `api_requests`, `api_latency_ms`, `retry_count`
- `error_count`, `devices_returned`
- `skip_reason`, `diagnostics`

Counts and durations are integers. Diagnostics are bounded categories, never
raw exception bodies, URLs, headers, responses, or credentials.

Example:

```text
collector_health,collector=fortigate,runtime=central,execution_mode=edge,status=skipped,health_owner=framework success=false,partial=false,duration_ms=0i,points_generated=0i,points_written=0i,skip_reason="configured_for_edge_runtime;current_runtime=central"
```

The scheduler produces this measurement for every registered native collector
execution. Collectors return summaries; they do not own health persistence.
