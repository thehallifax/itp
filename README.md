# ITP — Infrastructure Telemetry Platform

ITP discovers infrastructure, collects operational telemetry, stores it in
InfluxDB 3, and presents it through Grafana. It is designed for repeatable MSP
and consultant deployments: supported collectors ship with the repository and
are enabled through configuration, not code changes.

The project retains its established SNMP discovery, generated Telegraf inputs,
InfluxDB storage, Grafana dashboards, Docker Compose workflow, and native Mist
collector. The ITP name is an architectural evolution of the project, not a
rewrite.

## Quick start

```sh
git clone <repository-url> itp
cd itp
cp .env.example .env
cp discovery/config.example.yml discovery/config.yml
# Edit stack settings and collector enablement, then add only the credentials
# required by enabled collectors.
cp secrets/mist.env.example secrets/mist.env  # only when enabling Mist
cp secrets/fortigate.env.example secrets/fortigate.env  # only on a FortiGate edge
docker compose up -d --build
```

For a guided deployment, run `./scripts/install.sh` on Linux/macOS or
`./scripts/Install-ITP.ps1` on Windows. See [installation](docs/INSTALL.md),
[upgrades](docs/UPGRADING.md), and the [canonical schema](docs/SCHEMA.md).

Create an InfluxDB administrator token when bootstrapping a new deployment, then
set `INFLUXDB_TOKEN` in the root `.env` and restart the affected services. Never
commit `.env`, `secrets/*.env`, generated inventory, or tokens.

## Supported collectors

| Collector | Enablement | Telemetry path |
| --- | --- | --- |
| SNMP | `collectors.snmp.enabled: true` | Discovery → generated Telegraf inputs → InfluxDB |
| Juniper Mist | `collectors.mist.enabled: true` | Native HTTPS API → shared telemetry contract → InfluxDB |
| FortiGate | `collectors.fortigate.enabled: true` | Native edge HTTPS API → normalized and compatible telemetry → InfluxDB |

Every supported collector is included in the collector image. To enable one:

1. Add its credentials under `secrets/`.
2. Set `enabled: true` and configure its intervals/endpoints in
   `discovery/config.yml`.
3. Restart `collector` (and `discovery` for SNMP changes).

No source-code or image changes are required. A disabled native collector does
not require its secret file and the collector service remains healthy while
idle.

## Collector philosophy

A collector owns only authentication, discovery, and collection. Translating a
vendor response into the shared telemetry contract is the final collection
boundary. After that boundary, normalisation rules, inventory, storage, health
scoring, lifecycle tracking, change detection, dashboards, and reporting are
vendor-neutral.

Collectors must not contain dashboard logic, depend on Grafana, know about
other collectors, or invent collector-specific storage paths. See
[Architecture](docs/architecture.md) for the enforced boundaries.

## Configuration and credentials

- Root `.env`: Docker Compose interpolation, InfluxDB, Grafana, Telegraf, and
  ports.
- `secrets/*.env`: vendor credentials, injected only into the service that needs
  them. Examples are safe to commit; populated files are ignored and excluded
  from Docker build contexts.
- `discovery/config.yml`: collector enable flags, intervals, approved networks,
  and API endpoints. Start from `discovery/config.example.yml`.

`collectors/config.py` expands whole-value environment placeholders such as
`${MIST_ORG_ID}`. Unset placeholders become empty values; an enabled collector
then fails with the required variable names without exposing credential values.

Mist tenants are regional. Match `base_url` to the tenant portal—for example,
`manage.ac2.mist.com` uses `https://api.ac2.mist.com`. A wrong region commonly
appears as authentication or organization-access failure.

## Central and edge runtime modes

The same collector image runs in `central` or `edge` mode through
`ITP_RUNTIME_MODE`. It defaults to `central` for compatibility with existing Mist
deployments. Mist defaults to central placement; FortiGate API and SNMP default to edge.
Incompatible collectors are logged and skipped. See the
[edge architecture](docs/edge-collector-architecture.md).

```sh
cp secrets/fortigate.env.example secrets/fortigate.env
# Edit the secret file and enable collectors.fortigate with execution: edge.
ITP_RUNTIME_MODE=edge docker compose run --rm collector python -m collectors inspect fortigate
ITP_RUNTIME_MODE=edge docker compose run --rm collector python -m collectors collect fortigate
ITP_RUNTIME_MODE=edge docker compose up -d --force-recreate collector
docker compose logs --since=5m collector
```

The one-shot `collect` command writes telemetry and health. `inspect` validates API and
normalization behavior without writing telemetry. SNMP remains a fallback, interface
counter enrichment source, and option for devices without a suitable API. See the
[FortiGate collector guide](collectors/fortigate/README.md).

## Repository layout

```text
collectors/          collector contracts, registry, scheduler, inventory and writers
  snmp/              SNMP implementation and Telegraf generation
  mist/              native Mist API implementation
telemetry/           future vendor-neutral telemetry helpers
analysis/            future health, lifecycle and change analysis
dashboards/          version-controlled Grafana dashboards
runtime/inventory/   generated shared inventory and its documented contract
config/              configuration guidance, examples and template conventions
discovery/           compatible SNMP discovery command and deployment config
grafana/             Grafana provisioning
telegraf/            base and generated Telegraf configuration
docs/                concise architecture and roadmap documentation
```

The historical `discovery/discover.py` command and configuration paths remain in
place for deployment compatibility.

## Typical operations

```sh
python -m collectors list
docker compose run --rm discovery python /app/discover.py once --config /app/config.yml
docker compose run --rm collector python -m collectors inspect mist
docker compose up -d
docker compose logs --tail=200 discovery collector telegraf
```

Open Grafana at `http://localhost:${GRAFANA_PORT}`. The dashboard provider
recreates the operations-first hierarchy from `dashboards/`, including Network,
Compute, Printing, Services, Inventory, Collectors, Operations, and Vendor.
Start with **Infrastructure Overview** and use the Mist and FortiGate dashboards
under Vendor for engineering drill-down. See [dashboard navigation](docs/DASHBOARDS.md).
Live overview counts come from the deterministic
[Infrastructure State](docs/infrastructure-state.md); operational issues, risks,
and recommendations come from the [Operations Engine](docs/operations-engine.md).

### Inventory operations

The Inventory Engine mirrors collector observations into
`runtime/inventory/assets.json` while preserving the existing `devices.json`.
It provides stable source identity, reconciliation evidence, and lifecycle
evaluation without changing telemetry.

```sh
docker compose exec collector python -m collectors inventory summary
docker compose exec collector python -m collectors inventory list
docker compose exec collector python -m collectors inventory show ASSET_ID --json
docker compose exec collector python -m collectors inventory reconcile
docker compose exec collector python -m collectors inventory lifecycle
docker compose exec collector python -m collectors inventory sources
docker compose exec collector python -m collectors inventory history --limit 20
docker compose exec collector python -m collectors inventory retire ASSET_ID --reason "decommissioned"
docker compose exec collector python -m collectors inventory restore ASSET_ID --reason "returned to service"
docker compose exec collector python -m collectors inventory changes --since 7d --limit 50
docker compose exec collector python -m collectors inventory changes-summary --since 7d
```

See [runtime inventory](runtime/inventory/README.md) for identity rules,
lifecycle states, reconciliation states, persistence, and safe file inspection.

Lifecycle evaluation runs hourly by default. Assets age only after a complete,
successful discovery for their own source fails to observe them; collector
failures, disabled sources, partial discovery, and platform downtime do not age
inventory. Lifecycle remains CLI/file-visible and is not duplicated into
InfluxDB or Grafana in this milestone.

Inventory change detection runs during discovery ingestion and records only
meaningful normalized fields in `runtime/inventory/change_history.json`.
Protected serial, MAC, and source IDs are never silently replaced; conflicts are
high severity. Telemetry fluctuations and lifecycle state changes are excluded.

SNMP safety limits, credential rotation, troubleshooting, and generated-file
ownership are documented in [SNMP discovery](discovery/README.md). Mist mappings,
permissions, and collection scope are documented in the
[Mist collector guide](collectors/mist/README.md).

## Roadmap

The Inventory Engine, scheduled lifecycle, and inventory change-detection
foundations are implemented. The next milestones are Health Scoring,
Relationship Engine, and additional collectors. See the
[roadmap](docs/roadmap.md); it intentionally describes boundaries rather than
speculative implementations.
