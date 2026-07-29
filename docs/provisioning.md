# Automatic provisioning

`./itp deploy` and `./itp start` invoke the idempotent provisioning layer.

It creates required runtime, daemon, notification, dashboard, and provisioning
directories; generates managed Grafana provisioning and bundled dashboards;
checks the InfluxDB token; and creates the configured database when the service
is available.

Provisioning state is written atomically to:

```text
runtime/provisioning/state.json
```

The state records the provisioning version, status, missing prerequisites,
dashboard count, installed dashboard packs and versions, last attempt, and
last successful completion. It contains no
tokens, passwords, webhook URLs, or full environment values.

## Credential preservation

An existing non-empty `INFLUXDB_TOKEN` is never replaced. On a new local
InfluxDB 3 deployment, ITP attempts the supported one-time administrator token
bootstrap after the service starts, stores it in the ignored `.env`, and
recreates affected containers so they receive it. A failed bootstrap leaves
provisioning `partial`; it does not invent or silently regenerate a token.

## Partial recovery

Correct the item listed in `missing`, then rerun:

```sh
./itp start
```

Existing credentials and valid generated resources are preserved. Dashboard
files are regenerated deterministically from repository manifests, so upgrades
replace ITP-managed dashboards without affecting user-created Grafana content.
