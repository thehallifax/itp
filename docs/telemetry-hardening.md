# Telemetry hardening contract

ITP treats collectors as source adapters. A collector authenticates, discovers,
collects, and maps vendor responses, but the framework owns deployment
identity, schema validation, persistence, execution health, and readiness.

## Identity boundary

The deployment supplies `deployment_id`, `customer_id`, `site_id`, and
`site_name`. The writer replaces connector identity before a point reaches
InfluxDB. Vendor identity is retained as `source_site_id`, `source_site_name`,
`source_customer_id`, and other explicitly named source metadata.

`customer` and `site` remain temporary compatibility tags. They always equal
`customer_id` and `site_id`. Dashboards filter with canonical IDs and use names
only as labels.

## Write pipeline

```text
Discover → Collect → Adapt → Canonical identity → Coerce → Validate
         → Write → Framework health → Operational projections
```

The registry in `telemetry/contracts.py` controls fields whose Influx type must
remain stable. Shared coercers cover integer, float, boolean-to-integer,
numeric strings, and ISO-8601 timestamps. A rejected point reports the
measurement, field, expected type, received type, collector, and point number
without including credentials or source payloads.

## Collector health and runtime capabilities

The scheduler emits `collector_health` after every attempted discovery or
collection, including skipped and failed execution. It records runtime,
execution mode, duration, status, generated/written points, retries, skip
reason, bounded diagnostics, API latency, and request count.

Runtime writers discard legacy collector-generated health; the authoritative
record has `health_owner=framework`. Connector metadata declares supported
`central`, `edge`, or future `cloud` runtimes. Runtime mismatches remain visible
as skipped health and Doctor warnings.

## Migration

New writes use canonical identity immediately. Historical rows remain
queryable but may contain legacy site values. Do not silently combine canonical
and legacy identities. Regenerate managed dashboards after upgrading:

```bash
./itp dashboard generate
./itp restart
./itp doctor
```

For a clean baseline, deploy into a new database rather than rewriting
historical points.
