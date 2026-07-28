# MLC canonical baseline

Regenerate `runtime/mlc/capabilities/` before infrastructure, services,
wallboard and dashboard projections. These files are untracked runtime evidence.

The reference identity is deployment `mlc`, customer `mlc`, site `site:MLC`.
Local display-name overrides do not change these IDs. When adopting the identity
tag contract, reset the MLC telemetry database and regenerate runtime output so
legacy and canonical identity partitions are not mixed.

The `mlc` profile is ITP's authoritative single-site reference deployment.
Tracked profile and site metadata remains anonymised as **MLC Reference
Deployment** and **MLC Reference Site**. A production installation supplies its
display name and aliases through ignored `profiles/mlc/sites.local.yml`, and
connector endpoints through ignored `profiles/mlc/connectors.local.yml`.
Canonical identity remains `site:MLC`.

## Lifecycle

```text
profiles/mlc
  -> resolved connector configuration and profile secrets
  -> Palo Alto/PaperCut collection and SNMP discovery
  -> runtime/mlc/inventory
  -> runtime/mlc/infrastructure
  -> runtime/mlc/operations
  -> runtime/mlc/services
  -> runtime/mlc/dashboard
  -> profile-scoped Grafana provisioning
```

`runtime/mlc/` is generated and ignored. Never edit it as a source. The
profile-scoped database is `itp_mlc` in Docker volume
`itp-mlc_influxdb_data`; it must never be shared with another profile.

## Configure and generate

```sh
cp profiles/mlc/sites.yml profiles/mlc/sites.local.yml
cp profiles/connectors.local.example.yml profiles/mlc/connectors.local.yml
./itp profile init-secrets mlc
./itp profile validate mlc
./itp profile up mlc

./itp profile collect mlc paloalto
docker compose -p itp-mlc exec discovery \
  python /app/discover.py once --config /app/config.yml \
  --inventory /app/runtime/inventory/devices.json --generated /app/generated
docker compose -p itp-mlc exec collector \
  python -m collectors infrastructure generate
./itp profile operations mlc
./itp profile services mlc
./itp profile wallboard mlc
./itp profile dashboards mlc
```

PaperCut is normally a central collector while the reference container is edge
mode. When both roles are intentionally co-located for baseline validation, the
ignored connector override may set `execution: either`. Production deployments
should preserve the documented edge/central placement boundary.

## Clean reset

Stop only `mlc`, preserve credentials, remove only `runtime/mlc/` and the
profile's `itp-mlc_influxdb_data` volume, then rotate/bootstrap the profile
Influx administrator token before starting the profile again. Removing the
database volume is destructive and must not be generalized to other profiles.

After reset, validate that the earliest point is newer than the reset time,
each enabled collector has a successful current run, and all managed dashboards
have been regenerated. See [Measurement contracts](measurement-contracts.md).
