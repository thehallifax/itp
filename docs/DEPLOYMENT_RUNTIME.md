# Deployment runtime

This is the authoritative deployment path for a new ITP installation.
Operating-system details are in the [macOS](INSTALL_MACOS.md) and
[Linux](INSTALL_LINUX.md) guides.

## Prerequisites

- Git
- Python 3.9 or later
- Docker Desktop or Docker Engine with a running daemon
- Docker Compose v2 (`docker compose version`)
- Free TCP ports for Grafana and InfluxDB (defaults: 3000 and 8181)

Docker and Compose release lifecycles vary by operating system. Use a
vendor-supported Docker release that provides Compose v2.

## Clean installation

```bash
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

`./itp deploy` verifies Docker, asks for deployment identity and ports, creates
ignored runtime configuration, generates credentials, provisions managed
Grafana dashboards, builds images, starts the stack, and prints the Grafana
and InfluxDB URLs. InfluxDB storage is initialised by the container; no manual
database step is required.

For repeatable automation without enabling collectors:

```bash
./itp deploy \
  --non-interactive \
  --deployment-name "Example School" \
  --deployment-id example-school \
  --timezone UTC \
  --grafana-port 3000 \
  --influxdb-port 8181
```

The tracked `.env.example`, `discovery/config.example.yml`, and
`secrets/*.env.example` files document the legacy-compatible root configuration
contract. The deployment command does not copy or modify them. It writes the
active configuration beneath `runtime/deployments/<deployment-id>/`.

## Runtime layout

```text
runtime/deployments/<deployment-id>/
  deployment.yml
  collectors.yml
  dashboards.yml
  secrets/
  generated/
  logs/
  evidence/
  state/
runtime/shared/
runtime/backups/
```

`deployment.yml` owns identity, timezone, deployment mode, addresses, and
ports. `collectors.yml` owns enablement and non-secret connector configuration.
`generated/deployment.env` contains generated stack credentials and is
owner-readable only on macOS and Linux. Runtime files are ignored by Git.

## Verify health

```bash
./itp doctor
./itp status
./itp collector list
docker compose ls
```

Use `./itp deployment show --json` to inspect the resolved runtime paths when
directly inspecting a deployment. The CLI remains the preferred interface:

```bash
./itp logs --tail 200
./itp status --json
```

Grafana is available at the URL printed by deploy. InfluxDB and Grafana expose
container health checks; Doctor verifies platform configuration, Docker,
services, connector registration, and reachable health surfaces.

## Configure discovery and collectors

Fresh deployments intentionally enable no external collectors and do not
contact infrastructure. Configure one through the registry-driven workflow:

```bash
./itp collector list
./itp collector add <collector>
./itp collector test <collector>
./itp dashboard generate
./itp restart
```

The add command writes non-secret discovery/collector settings to
`collectors.yml` and credentials to the deployment's ignored `secrets/`
directory. Required placeholders fail through collector validation with an
actionable message. Optional fields and connector-specific prerequisites are
documented in [Collector onboarding](COLLECTOR_ONBOARDING.md).

## Stop, restart, and recover

```bash
./itp stop
./itp start
./itp restart
./itp status
```

Stopping removes containers but preserves named Docker volumes and the runtime
deployment. Starting again reuses both. No source file should change during
deployment or lifecycle operations.

## Backup and upgrade

Back up the complete `runtime/` directory and the deployment's named Docker
volumes before an upgrade. Runtime configuration alone does not contain
InfluxDB or Grafana volume contents.

```bash
./itp update
./itp doctor
./itp status
```

Update requires a clean source checkout, performs a fast-forward-only pull,
rebuilds images, and preserves the active runtime deployment. See
[Upgrade](UPGRADE.md) for rollback guidance.

## Secrets

Never commit `runtime/`, populated environment files, credentials, support
evidence, or database exports. Bind services to localhost unless remote access
is explicitly protected. See [Security and secrets](SECURITY_AND_SECRETS.md)
for storage, permissions, rotation, and disclosure guidance.
