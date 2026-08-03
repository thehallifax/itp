# Troubleshooting

Use deployment-aware commands and replace `<deployment>` with the runtime ID.

## Grafana provisioning permissions

If Grafana restarts with `permission denied` or `Failed to create provisioner`:

```sh
./itp doctor --deployment <deployment>
find runtime/deployments/<deployment>/generated/dashboard -exec ls -ld {} \;
./itp restart --deployment <deployment>
```

Generated dashboard files and `provisioning/dashboards.yml` must be `0644`;
shared dashboard directories must be `0755`. Secrets remain `0600`. Restart
rebuilds current images with `--build --remove-orphans`.

On Windows PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 doctor --deployment <deployment>
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 restart --deployment <deployment>
Get-ChildItem runtime\deployments\<deployment>\generated\dashboard -Recurse
```

Windows ACLs, rather than POSIX mode bits, are authoritative.

## Dashboard does not refresh

```sh
./itp logs collector --deployment <deployment>
./itp dashboard generate --deployment <deployment>
```

Confirm `dashboard.render.begin` and `dashboard.render.complete`, then inspect
the modification time beneath
`runtime/deployments/<deployment>/generated/dashboard/managed/`. If the events
are absent after an update, restart to rebuild the collector image.

## Missing service cards

Operational dashboards hide capabilities that were never enabled. Inspect and
amend the deployment with:

```sh
./itp collector list --deployment <deployment>
./itp collector add <collector> --deployment <deployment>
./itp collector test <collector> --deployment <deployment>
```

Discovery alone never enables a capability.

## WAN role not configured

Inspect Palo Alto interfaces, then add `wan_interfaces` to the deployment's
ignored `collectors.yml` as documented in the
[Palo Alto guide](collectors/paloalto.md). Rerun the collector and regenerate
dashboards. Do not infer WAN state from an interface number or default route.

If the role is configured but throughput says **Awaiting first collection**,
run two observations far enough apart for the cumulative counters to change:

```sh
./itp collector run paloalto --deployment <deployment>
./itp collector run paloalto --deployment <deployment>
./itp dashboard generate --deployment <deployment>
```

The first observation is a baseline. Counter reset, missing counters, and a
restart without a prior inventory baseline safely defer the rate. The stable
query key is the PAN-OS interface name; `display_name` only labels the panel.

## Partial or interrupted deployment

```sh
./itp deployment list
./itp doctor --deployment <deployment>
./itp reset --deployment <deployment>
./itp deploy --deployment-id <deployment> --force
```

Reset preserves telemetry by default. If an abandoned InfluxDB bootstrap
created an operator token that cannot be recovered and the deployment is
confirmed disposable, explicitly use:

```sh
./itp reset --deployment <deployment> --reset-influx
```

This is destructive and requires confirmation. A missing canonical site is
corrected by rerunning deployment with `--site-id`; new sites are created
idempotently before scheduler startup.

## Old Docker Desktop ITP projects

`docker compose ls` may omit stopped projects. ITP audits exact Compose labels:

```sh
./itp cleanup
./itp cleanup --yes
```

The first command is a dry run. Cleanup preserves volumes, unrelated projects,
unknown resources and global build cache. It may suggest `docker builder prune
-a`, but never executes it.

## Empty data

Use Collector Health and Doctor to distinguish Configuration required,
Awaiting first collection, Data stale, Feature unavailable, Collector failed,
and No matching records. A bare Grafana `No data` response is not a platform
health decision.
