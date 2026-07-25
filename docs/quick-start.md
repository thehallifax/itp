# ITP Quick Start

This guide prepares a new root Docker Compose deployment. Use deployment
profiles when multiple isolated customer estates must run on the same host.

## Requirements

- Docker Desktop or Docker Engine
- Docker Compose v2 (`docker compose version`)
- Python 3.9 or later
- Available TCP ports 3000 and 8181 by default

Clone ITP and run the bootstrap wizard:

```sh
git clone https://github.com/thehallifax/itp.git
cd itp
./itp setup
./itp doctor
./itp start
./itp status
```

On Windows PowerShell:

```powershell
.\itp.ps1 setup
```

The launcher locates Python, creates `.venv`, and installs runtime dependencies
on the first invocation. It synchronises dependencies only when the tracked
project definition changes. Activation and global package installation are not
required.

The wizard asks for the deployment name, deployment type, and Grafana port. It
then:

1. Verifies Docker and Docker Compose.
2. Checks the Grafana and InfluxDB ports.
3. Creates `.env` and `discovery/config.yml` from tracked examples when absent.
4. Adds deployment metadata and the selected Grafana port.
5. Runs `docker compose config --quiet`.
6. Optionally starts the platform and waits for its services to become ready.

When startup completes, open the printed dashboard URL, normally
`http://localhost:3000`.

## Evaluate with demo data

If you want to evaluate the interface before configuring real connectors, run:

```sh
./itp demo
```

On Windows PowerShell:

```powershell
.\itp.ps1 demo
```

The demo is a separate Compose project on Grafana port 3300 and InfluxDB port
8281. It provisions dashboard packs and seeds 30 days of deterministic
telemetry, pipeline runs, and notification history. It cannot target the root
deployment database or runtime directory. Continue with
[Demo Environment](demo.md) for details.

## Automated setup

All prompts can be replaced with command-line options:

```sh
./itp setup --non-interactive \
  --deployment-name "North Campus" \
  --deployment-type "School" \
  --grafana-port 3000 \
  --start
```

Without `--start`, setup validates the deployment and prints the command needed
to start it. `--health-timeout` changes the default 180-second startup wait.

## Safe reruns

Running setup again does not overwrite `.env` or `discovery/config.yml`.
Interactive mode asks before updating the fields owned by the wizard. For
automation, `--force` explicitly permits updates to:

- `GRAFANA_PORT` in `.env`
- `deployment.name` and `deployment.type` in `discovery/config.yml`
- `customer` and `site` slugs derived from the deployment name

Other environment variables and configuration sections are retained.

## Enable collectors

The wizard does not request or store credentials. After setup:

1. Copy the required vendor file from `secrets/*.env.example` to its matching
   ignored `.env` file.
2. Add credentials only to that local secret file.
3. Set the collector's `enabled` value in `discovery/config.yml`.
4. Restart the collector service:

   ```sh
   docker compose restart collector
   ```

Never commit `.env`, `discovery/config.yml`, populated files under `secrets/`,
or generated files under `runtime/`.

Inspect all implemented connector boundaries before manual onboarding:

```sh
python -m collectors connectors list
python -m collectors connectors inspect mist
```

OOBE-001 bootstraps the platform. The registry introduced by OOBE-002 supplies
future onboarding metadata, but Phase 1 does not prompt for vendor credentials.
Connectors marked manual or profile-only are deliberately not presented as
guided options.

For multi-customer deployments, continue with
[deployment profiles](deployment-profiles.md).

## Diagnose setup

```sh
./itp doctor --offline
./itp doctor
```

The first command checks local files without Docker access. The second includes
containers and known local HTTP health endpoints. Use
`./itp doctor --json --strict` for support automation. Doctor is read-only and
reports credential presence without displaying values.

Docker Compose is an implementation detail for normal operation. Use
`./itp start`, `./itp stop`, `./itp restart`, and `./itp logs`; see
[Deployment](deployment.md).

## Collect and inspect status

Run every enabled connector once using the platform scheduler:

```sh
./itp collect
./itp collect --json
```

The summary identifies successful, failed, disabled, and runtime-unavailable
connectors. Each run records deterministic `PipelineRun` metadata under
`runtime/pipeline-runs/`; connector result details are restricted to
non-sensitive operational counters.

Inspect the current deployment without running a connector:

```sh
./itp status
./itp status --json
```

Status combines registry metadata, the latest collection run, and generated
service health. Connector freshness is reported as `Fresh`, `Stale`, `Unknown`,
`Never Run`, `Failed`, or `Disabled`. Existing profile-specific workflows
continue to use `./itp profile status` and `./itp profile collect`.

## Continuous operation

Start scheduled collection in the background:

```sh
./itp daemon
./itp status
```

Each connector uses its existing `discovery_interval_seconds` and
`collection_interval_seconds` configuration. The daemon uses an exclusive PID
lock under `runtime/daemon/`, maintains a durable heartbeat, and records
scheduled collection outcomes as canonical pipeline runs. A failed connector
does not stop other connectors.

Use foreground mode with a service manager or container:

```sh
./itp daemon --foreground
```

`SIGINT` and `SIGTERM` stop scheduling cleanly and persist a final `Stopped`
state. For automation that needs exactly one lock-protected cycle:

```sh
./itp daemon --once
```

Only non-sensitive counters and exception types are written to daemon state,
pipeline results, or command output. Background runtime logs are written to
`runtime/daemon/daemon.log`.

## Notifications

Notifications are opt-in. Configure `notifications` in
`discovery/config.yml`, then validate delivery before enabling operational
evaluation:

```sh
./itp notifications test
./itp notifications evaluate
./itp notifications list
./itp status
```

Use `--json` with any notification command for automation. Webhook URLs and
authorization headers should be supplied through the environment placeholders
shown in `.env.example`; they are never included in notification state or
delivery errors. See [Notifications](notifications.md) for configuration,
deduplication, recovery, acknowledgement, and troubleshooting.

## Bootstrap troubleshooting

- **Python is not installed:** install Python 3.9 or later from your operating
  system package manager or [python.org](https://www.python.org/downloads/),
  then rerun `./itp` or `.\itp.ps1`.
- **Python is unsupported:** upgrade to Python 3.9 or later. The launcher does
  not install or modify system Python.
- **PowerShell blocks the launcher:** permit the current session with
  `Set-ExecutionPolicy -Scope Process Bypass`, then rerun `.\itp.ps1`.
- **Installation is offline:** the first run needs the packages declared by
  `pyproject.toml`. Connect temporarily, configure an internal Python package
  index, or pre-populate pip's package cache, then rerun the command.
- **An interrupted installation left `.venv` incomplete:** rerun the command.
  ITP safely replaces only the repository-local incomplete environment. If a
  process is locking it, close that process, remove `.venv`, and retry.

Bootstrap progress and installation diagnostics are written to stderr, so
commands such as `./itp status --json` keep stdout machine-readable.
