# Operator guide

## Single-site deployment

```bash
./itp profile create customer
./itp profile init-secrets customer
./itp profile validate customer
./itp profile up customer
./itp profile status customer
```

## Multi-site deployment

Create one profile, adapt a tracked deployment example, configure collector
aliases, then run:

```bash
./itp profile sites customer
./itp profile validate customer
./itp profile up customer
./itp profile services customer
./itp profile dashboards customer
./itp profile status customer
```

Confirm Entire Estate and each site appear, then compare a drill-down with the
estate view.

## Updating

```bash
git pull --ff-only
./itp profile validate <profile>
./itp profile restart <profile>
./itp profile status <profile>
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
