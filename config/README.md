# Configuration layout

- Root `.env` contains stack and deployment settings only.
- `secrets/*.env` contains local vendor credentials; commit only `.env.example`
  files.
- `discovery/config.yml` contains collector enablement, intervals, networks, and
  endpoints. Its historical location is retained for compatibility.
- `config/examples/` documents safe example conventions.
- `config/templates/` is reserved for reusable generated configuration templates.

Inventory defaults:

```yaml
inventory:
  enabled: true
  persistence_path: /app/runtime/inventory
  stale_after_seconds: 86400
  missing_after_seconds: 604800
  lifecycle_evaluation_interval_seconds: 3600
  lifecycle_history_max_events: 10000
  lifecycle_history_retention_days: 365
  preserve_legacy_outputs: true
  change_detection:
    enabled: true
    history_max_events: 20000
    history_retention_days: 365
    duplicate_suppression_seconds: 3600
    ignored_fields: []
    minimum_severity: info
```

The compatibility output remains enabled. Threshold changes take effect when
lifecycle evaluation runs, but only complete successful source discoveries can
provide aging evidence. History defaults to 10,000 events and 365 days.

Change detection defaults to 20,000 events, 365 days, and a one-hour duplicate
suppression window. Removal events require source-declared authoritative fields;
configuration suppression never changes collector behavior or telemetry.
