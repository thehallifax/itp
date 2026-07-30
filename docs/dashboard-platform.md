# Dashboard platform

## Static packs and runtime snapshots

Connector dashboards such as Palo Alto and PaperCut are static managed
templates. They are regenerated when collector enablement, pack versions, or
templates change.

The Operations Wallboard, Infrastructure Overview, and Collector Health are
state-derived platform dashboards. Their snapshot inputs live beneath the
selected `ITP_RUNTIME_DIR`:

- `inventory/source_runs.json`
- `infrastructure/state.json`
- `operations/operations.json`
- `services/service-health.json`

The scheduler refreshes these dashboards after the canonical analysis cycle
and publishes them to
`runtime/deployments/<deployment>/generated/dashboard/managed/`, the exact
directory provisioned into Grafana. A normal cycle logs
`dashboard.render.begin` followed by `dashboard.render.complete`. Generated JSON
is validated before an atomic replacement. If rendering fails, the previous
valid dashboard remains in place and `dashboard.render.failed` is logged.
Grafana's managed file provider detects successful replacements on its normal
polling interval.

ITP dashboards are selected from collector manifests rather than a static
vendor list. The registry reads `collectors/*/dashboard-manifest.yml`, merges
capabilities for enabled collectors, and materializes selected dashboards under
`runtime/dashboard/managed/`.

## Manifests

Each collector manifest declares:

- collector name and manifest version
- capabilities
- collector dependencies
- dashboard source, stable UID, folder, tags and required capabilities

Adding a collector requires only its collector package, manifest and dashboard
JSON. Registry code and Grafana provisioning do not contain vendor branches.

Supported capabilities are `firewall`, `internet`, `wireless`, `switching`,
`printing`, `identity`, `compute`, `inventory`, and `telemetry`.

## Selection

These dashboards are always managed:

- Operations Wallboard
- Infrastructure Overview
- Collector Health

Vendor and future capability packs are selected only when their collector is
enabled and their required capabilities are present. Disabled collector
dashboards are removed from the managed namespace.

```sh
docker compose exec collector python -m collectors dashboards status
docker compose exec collector python -m collectors dashboards generate
```

Generation is deterministic. The scheduler refreshes the registry after state,
operations, and wallboard rendering so managed copies receive current embedded
data.

## Folders and ownership

Grafana receives eight fixed folders:

`Operations`, `Infrastructure`, `Security`, `Wireless`, `Printing`, `Compute`,
`Identity`, and `Vendor`.

Generated dashboards carry `itp-managed`, `itp-collector:*`, and
`itp-capability:*` tags, have stable UIDs, and disallow UI updates. Upgrades can
replace or remove only files in `runtime/dashboard/managed/`.

User-created dashboards remain in Grafana's database and are outside that
filesystem namespace. Do not place user dashboards inside the managed runtime
directory.

The capability registry is written to
`runtime/dashboard/managed/registry.json`. The Operations Wallboard reads this
registry to distinguish enabled domains from unavailable telemetry without
checking vendor names.

## Empty states and onboarding

Managed dashboard generation also writes the canonical readiness contract to
`runtime/dashboard/readiness.json`. Infrastructure Overview, Operations
Wallboard and Collector Health use it to distinguish not configured, awaiting
first collection, unavailable, healthy, warning and critical states.

Infrastructure Overview includes a generated Setup Status table. Empty
Collector Health tables contain an explanatory row, while string Stat panels
retain their values directly instead of relying on Grafana's numeric reducer or
generic `No data` rendering. See [Readiness and dashboard empty
states](readiness.md).
