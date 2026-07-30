# ITP Quick Start

This guide starts from a clean clone. ITP stores deployment configuration,
credentials, generated dashboards, and persistent state under the ignored
`runtime/` directory.

## Prerequisites

- Git
- Python 3.9 or later
- Docker Desktop or Docker Engine
- Docker Compose v2

## Install and validate

```sh
git clone https://github.com/<organisation>/<repository> infrastructure-telemetry-platform
cd infrastructure-telemetry-platform
./itp deploy
```

The deployment wizard verifies Docker, selects available ports, writes
owner-only runtime configuration, provisions managed dashboards, starts the
stack, and prints the Grafana URL. All external collectors remain disabled
until explicitly configured.

On Windows PowerShell, use:

```powershell
.\itp.ps1 deploy
```

The Windows launcher performs runtime bootstrap automatically. See the
[Windows installation guide](INSTALL_WINDOWS.md) for prerequisites,
PowerShell policy guidance, and contributor setup.

## Verify the deployment

```sh
./itp doctor
./itp status
./itp collector list
```

To evaluate the interface without live infrastructure:

```sh
./itp demo
```

Demo data uses an isolated deployment and cannot overwrite a production
database.

## Add a collector

```sh
./itp collector add snmp
./itp collector test snmp
./itp dashboard generate
./itp restart
```

The CLI reports any connector-specific configuration still required. Never
commit files under `runtime/`, populated secret files, or local environment
files.

Continue with:

- [Deployment runtime](DEPLOYMENT_RUNTIME.md)
- [Collector onboarding](COLLECTOR_ONBOARDING.md)
- [Security and secrets](SECURITY_AND_SECRETS.md)
- [Doctor and troubleshooting](doctor.md)
