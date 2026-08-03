# Deployment

## Clone to running

Prerequisites are Git, Python 3.9 or later, Docker Desktop or Docker Engine,
and Docker Compose v2.

```sh
git clone https://github.com/<organisation>/<repository> infrastructure-telemetry-platform
cd infrastructure-telemetry-platform
./itp deploy
```

Deploy creates ignored runtime configuration, provisions managed resources,
starts the stack, and prints the Grafana URL. The launcher bootstraps runtime
dependencies without installing contributor-only test packages. Developers
should follow [CONTRIBUTING.md](../CONTRIBUTING.md).

On a clean deployment, Infrastructure Overview opens with a generated Setup
Status checklist. **Monitoring not started** means the platform is operating
normally but no external collector is enabled. Enablement, first collection,
inventory and operational-analysis progress update after managed dashboard
regeneration. See [Readiness and dashboard empty states](readiness.md).

On Windows PowerShell, use the equivalent launcher:

```powershell
.\itp.ps1 deploy
.\itp.ps1 doctor
.\itp.ps1 status
```

The PowerShell launcher performs the same runtime bootstrap as `./itp`; do not
run `scripts\bootstrap.py` separately. See the
[Windows installation guide](INSTALL_WINDOWS.md).

Deployment is transactional at the configuration boundary. ITP gathers and
validates deployment identity, ports, timezone, canonical site and credentials,
prints a plan, and asks for confirmation before creating persistent files or
Docker resources. InfluxDB bootstrap begins only after `deployment.env` has
been written durably. Completion is reported only after services and Doctor
pass their required checks.

The canonical site defaults to the deployment ID. A separate display name may
be changed without changing identity:

```sh
./itp deploy --deployment-id campus \
  --site-id north-campus --site-name "North Campus"
```

`deployment_id` owns the runtime; `site:...` partitions canonical telemetry;
the display name is presentation metadata; collectors are instances within the
deployment; and Docker uses the exact Compose project `itp-<deployment-id>`.

If local script execution is blocked:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

For a single process without a persistent policy change:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 deploy
```

Use `pwsh` instead of `powershell.exe` for PowerShell 7. An execution policy
enforced by organisational Group Policy requires administrator assistance.

## Lifecycle commands

```sh
./itp start [--json]
./itp stop [--json]
./itp restart [--json]
./itp logs [--follow] [--service <service>] [--tail <lines>]
```

Commands wrap the repository's existing Compose file. Repeated start and stop
operations are safe. Lifecycle output reports the resulting service state and
never prints environment values or credentials.

Deployment recovery and removal are explicit:

```sh
./itp deployment list
./itp reset --deployment campus
./itp reset --deployment campus --reset-influx
./itp remove --deployment campus
./itp remove --deployment campus --remove-telemetry
./itp cleanup
./itp cleanup --yes
```

`deployment list` reports configured, stopped, partial and orphaned state even
when `docker compose ls` is empty. Reset preserves the InfluxDB volume unless
`--reset-influx` is explicit. Remove likewise preserves telemetry unless
`--remove-telemetry` is explicit. Cleanup is a dry-run by default, removes only
exactly labelled orphan containers and networks, preserves volumes and unknown
Docker resources, and never runs a global Docker or builder prune.

## Dashboard packs

Deploy and start resolve dashboard packs from enabled connector metadata.
Platform dashboards are always installed; Infrastructure Overview is the
default Grafana landing page. The initial connector example is the SNMP pack,
which provides device totals, availability, and canonical SNMP inventory.

Managed dashboards use stable UIDs and carry `itp-managed` plus pack-version
tags. Upgrades replace managed content with the same UID. Disabling a connector
removes its files only from
`runtime/deployments/<deployment>/generated/dashboard/managed/`; dashboards created
through Grafana are stored separately and are not removed. Installed packs and
versions appear under `stack.dashboard_packs` in `./itp status --json`.

## Troubleshooting

- Run `./itp doctor --offline` for filesystem/configuration checks.
- Start Docker if the CLI reports that its daemon is unavailable.
- Run `./itp status --json` for Compose, InfluxDB, Grafana, daemon, and
  provisioning state.
- Use `./itp logs --service <service> --tail 200`.
- Rerun `./itp start` to recover partial provisioning safely.
- If bootstrap cannot locate Python, install Python 3.9 or later.
- If PowerShell blocks `itp.ps1`, use `RemoteSigned` at `CurrentUser` scope or
  the process-scoped command above.
- If Windows App Execution Aliases redirect Python to the Microsoft Store,
  install Python from python.org and disable the conflicting aliases.
- Windows stack commands distinguish a missing Docker command, missing Compose
  v2 plugin, and a stopped Docker Desktop daemon.
- Dependency installation requires package-index access on the first run.
  Offline deployments must provide pip's cache or an internal package index.

Profile-scoped Compose commands remain available for existing multi-customer
deployments.
