# Deployment modes

Standalone and cluster-member profiles share one schema. Execution evidence is
written only inside the member profile runtime namespace.

Every profile declares exactly one deployment mode.

## Standalone

```yaml
deployment:
  mode: standalone
```

A standalone profile owns its collector and discovery runtimes, InfluxDB,
Grafana, Telegraf, profile runtime directory, secrets namespace, database and
published ports. `profile up` performs validation and a port preflight before
starting anything. A conflict identifies the publishing Compose project or
container where Docker can provide it. Grafana and collectors receive the same
profile database and timezone.

## Cluster member

```yaml
deployment:
  mode: cluster_member
  cluster_id: managed-cluster
  shared_services:
    grafana_url: https://grafana.example.invalid
    influxdb_url: https://influx.example.invalid
```

A cluster member starts only customer-scoped collector and discovery services.
It never starts profile-local Grafana or InfluxDB and never falls back to
standalone mode. Shared endpoints and `cluster_id` are mandatory and are checked
before startup. The profile database remains the tenant namespace supplied to
collectors and shared Grafana provisioning.

`profile status` identifies the mode, actual profile containers and shared
endpoints. `profile logs` always uses the profile's `itp-<profile>` Compose
project. Customer isolation consists of a unique deployment/customer identity,
runtime directory, secrets directory, database and dashboard namespace.

This contract is the minimum cluster-member boundary; it does not implement an
MSP control plane.
