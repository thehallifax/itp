# Demo Environment

The ITP demo provides a reproducible estate for evaluation, screenshots,
training, and dashboard development. It does not require access to any real
device or vendor API.

## Start

Docker Engine or Docker Desktop with Compose v2 must be available. From the
repository root, run:

```sh
./itp demo
```

When seeding completes, open Grafana at
[http://localhost:3300](http://localhost:3300). The initial run builds and
starts an isolated stack, so it can take several minutes.

The optional history and seed settings are deterministic:

```sh
./itp demo --days 30 --seed 1001
./itp demo --json
```

History may be set from 1 to 90 days. A rerun with the same seed and persisted
time window writes the same series keys and timestamps; it does not append
random duplicates.

## Included data

The generated estate includes:

- Telegraf-style host CPU, memory, and uptime
- Canonical SNMP device, availability, and performance records
- Mist devices and wireless access points
- FortiGate system, performance, and interface counters
- Collector health with healthy, warning, and failed periods
- Daily canonical PipelineRun history
- Informational, warning, critical, and recovery notification history

Telemetry is hourly across approximately 30 days by default. Names, addresses,
serial-like identifiers, counters, failures, and notification content are
fictional.

## Isolation contract

Demo mode refuses to run unless all of these fixed boundaries are active:

| Resource | Demo value |
| --- | --- |
| Compose project | `itp-demo` |
| InfluxDB database | `itp_demo` |
| Runtime directory | `runtime/demo/` |
| Grafana port | `3300` |
| InfluxDB port | `8281` |
| Deployment ID | `demo` |

Docker Compose therefore creates separate project-scoped volumes. Demo
configuration, its generated token, dashboard copies, notifications, and
pipeline records stay under the gitignored `runtime/demo/` tree. The command
never reads a production collector credential and never writes to the root
deployment database.

If either demo port is occupied, the command stops with an actionable error
instead of falling back to production ports.

## Stop or remove

Stop demo containers while retaining their data:

```sh
docker compose -p itp-demo down
```

To permanently remove only demo containers and project volumes:

```sh
docker compose -p itp-demo down --volumes
```

The generated files under `runtime/demo/` remain local and gitignored. Remove
that directory only when its demo history is no longer needed.

## Troubleshooting

Use `./itp demo --json` for a machine-readable seed summary. For container
diagnostics:

```sh
docker compose -p itp-demo ps
docker compose -p itp-demo logs --tail 200
```

Demo mode deliberately does not simulate live vendor APIs. It seeds the same
InfluxDB measurements and platform state contracts consumed by managed
dashboards.
