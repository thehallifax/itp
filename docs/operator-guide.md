# Operator guide

## Single-site deployment

```bash
./itp deploy --deployment-id customer --site-id main-campus
./itp collector add <collector> --deployment customer
./itp collector test <collector> --deployment customer
./itp collect --deployment customer
./itp doctor --deployment customer
./itp status --deployment customer
```

## Multi-site deployment

Create one runtime deployment and define its canonical sites. The deployment
wizard creates the first site idempotently; additional site hierarchy remains
an advanced profile workflow described in
[deployment profiles](deployment-profiles.md).

```bash
./itp deployment show customer
./itp doctor --deployment customer
./itp dashboard generate --deployment customer
./itp status --deployment customer
```

Confirm Entire Estate and each site appear, then compare a drill-down with the
estate view.

## Updating

```bash
git pull --ff-only
./itp update --deployment <deployment>
./itp doctor --deployment <deployment>
./itp status --deployment <deployment>
```

## Troubleshooting

- **Site missing:** check `enabled`, aliases and collector site values.
- **Duplicate alias:** make each normalized alias unique inside the profile.
- **Parent not found:** use an ID from the same profile.
- **No estate data:** regenerate infrastructure, services and dashboards.
- **Wrong parent:** inspect `runtime/<profile>/sites/hierarchy.json`.
- **Wrong profile:** verify `ITP_PROFILE`, `deployment_id`, Compose project and mounts.
- **Incorrect central impact:** correct the explicit dependency; ITP never infers one.

Back up configuration, ignored secrets, Grafana and InfluxDB volumes, and the
profile runtime directory together. Never restore one customer's data into
another profile.

## Virtualisation

```bash
./itp profile virtualisation <profile> --fixture vmware
./itp profile virtualisation <profile> --fixture hyperv
./itp profile virtualisation <profile> --fixture proxmox
./itp profile virtualisation-status <profile>
```

Copy only the required profile secret example, use a read-only account and keep
TLS verification enabled. Unknown means evidence was insufficient, not that a
workload is down. Inspect `runtime/<profile>/virtualisation/collection-status.json`
for partial or permission-limited sections.

Interpret `Unknown` as an observability limitation. Do not restart workloads
because a manager is unreachable. Confirm direct host, storage and workload
evidence first. Critical shared-storage or expected-running workload findings
identify the evidence and affected canonical service; recommendations remain
read-only operator guidance.

For release evidence, run `scripts/render_wallboard_scenario.py` with `example-corporate`,
`vmware`, `hyperv` or `proxmox`. Do not copy evidence output into
`runtime/<profile>`: it is an isolated screenshot fixture, not production
telemetry. The live dashboard is under **Operations → Operations Wallboard**
at `/d/itp-operations-wallboard`.
