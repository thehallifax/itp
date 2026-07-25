# Notifications

ITP notifications consume existing canonical state. They do not poll devices,
schedule collectors, or implement separate health checks.

## Configuration

Add the optional section below to root or profile discovery configuration:

```yaml
notifications:
  enabled: true
  minimum_severity: warning
  repeat_suppression_seconds: 3600
  daemon_heartbeat_stale_seconds: 30
  channels:
    console:
      enabled: true
    webhook:
      enabled: false
      url: ${NOTIFICATION_WEBHOOK_URL}
      timeout_seconds: 5
      headers:
        Authorization: ${NOTIFICATION_WEBHOOK_AUTHORIZATION}
```

Supported minimum severities are `info`, `warning`, and `critical`. Omitting
the section preserves previous behaviour and disables operational delivery.
Keep webhook URLs and authorization values in local environment files, never
in tracked YAML.

## Commands

```sh
./itp notifications evaluate
./itp notifications list
./itp notifications inspect <notification-id>
./itp notifications acknowledge <notification-id>
./itp notifications test
```

Every command supports `--json`. Evaluation runs the existing Doctor and reads
the existing operator status, freshness, daemon state, connector registry, and
latest `PipelineRun`. `test` creates a clearly marked delivery-only event and
never changes active incident state.

## Deduplication and recovery

Each condition has a SHA-256 fingerprint derived from its rule, stable subject,
and scope. Repeated evaluations update `last_seen` and `occurrence_count` on
the same active notification. Repeat deliveries are recorded as `suppressed`
until `repeat_suppression_seconds` expires.

When a condition clears, ITP closes the active notification and emits a
`recovery` event linked through `recovery_of`. Acknowledgement records operator
action but does not hide an unresolved condition. State is atomically persisted
under `runtime/notifications/`.

## Webhook payload

Generic webhooks receive an HTTP JSON POST:

```json
{
  "schema_version": 1,
  "event": {
    "id": "notification:...",
    "severity": "critical",
    "title": "Connector collection failed",
    "summary": "Juniper Mist collection failed.",
    "subject": "mist",
    "first_seen": "2026-07-24T08:00:00Z",
    "occurrence_count": 1
  }
}
```

The actual event includes the complete non-sensitive notification schema.
Connector configuration, exception messages, webhook URLs, and request headers
are excluded. Delivery failures retain only the exception type and the generic
detail `delivery failed`.

## Troubleshooting

- Run `./itp notifications test --json` to inspect per-channel delivery state.
- Confirm notification and channel `enabled` flags independently.
- Confirm environment placeholders expand inside the running process.
- Use `./itp notifications inspect <id> --json` for incident history.
- Review `failed_delivery_count` in `./itp status --json`.

Phase 1 intentionally excludes Teams, Slack, and email-specific adapters.
