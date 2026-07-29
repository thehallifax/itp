# Deployment

## Clone to running

Prerequisites are Git, Python 3.9 or later, Docker Desktop or Docker Engine,
and Docker Compose v2.

```sh
git clone https://github.com/<organisation>/<repository> infrastructure-telemetry-platform
cd infrastructure-telemetry-platform
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
python -m pytest -q
./itp deploy
```

Deploy creates ignored runtime configuration, provisions managed resources,
starts the stack, and prints the Grafana URL. The launcher can bootstrap its
own environment for normal operation, while the explicit environment above
makes dependency installation and tests independently verifiable.

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

## Dashboard packs

Deploy and start resolve dashboard packs from enabled connector metadata.
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
