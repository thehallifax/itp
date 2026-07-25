# Deployment

## Clone to running

Prerequisites are Git, Python 3.9 or later, Docker Desktop or Docker Engine,
and Docker Compose v2.

```sh
git clone <repository>
cd <repository>
./itp setup
./itp doctor
./itp start
./itp status
```

Setup creates local configuration from tracked examples and offers to provision
and start the stack. If declined, it prints `./itp start` as the next command.
The `./itp` launcher creates and synchronises a repository-local `.venv`
automatically; users do not activate it or install packages globally.

On Windows PowerShell, use the equivalent launcher:

```powershell
.\itp.ps1 setup
.\itp.ps1 doctor
.\itp.ps1 start
.\itp.ps1 status
```

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

## Dashboard packs

Setup and start resolve dashboard packs from enabled connector metadata.
Platform dashboards are always installed; Infrastructure Overview is the
default Grafana landing page. The initial connector example is the SNMP pack,
which provides device totals, availability, and canonical SNMP inventory.

Managed dashboards use stable UIDs and carry `itp-managed` plus pack-version
tags. Upgrades replace managed content with the same UID. Disabling a connector
removes its files only from `runtime/dashboard/managed/`; dashboards created
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
- If PowerShell blocks `itp.ps1`, run
  `Set-ExecutionPolicy -Scope Process Bypass` for the current session.
- Dependency installation requires package-index access on the first run.
  Offline deployments must provide pip's cache or an internal package index.

Profile-scoped Compose commands remain available for existing multi-customer
deployments.
