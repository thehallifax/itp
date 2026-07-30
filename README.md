# Infrastructure Telemetry Platform

ITP turns infrastructure collectors into vendor-neutral inventory, telemetry,
service health, operational findings, notifications, and managed Grafana
dashboards.

Deployment identity, telemetry validation, collector execution health, and
dashboard scoping are framework-owned, so vendor site names cannot fragment
operational views.

## Deploy

The platform launcher creates `.venv` and installs runtime dependencies
automatically.

### Windows PowerShell

```powershell
git clone https://github.com/thehallifax/infrastructure-telemetry-platform.git
cd infrastructure-telemetry-platform
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 deploy
```

This process-only execution-policy override does not permanently change the
machine or user policy. On systems where local scripts are already allowed,
the shorter `.\itp.ps1 deploy` command is equivalent. The launcher can offer
to install Python 3.12 through WinGet when no supported Python is available.

### macOS or Linux

```sh
git clone https://github.com/thehallifax/infrastructure-telemetry-platform.git
cd infrastructure-telemetry-platform
./itp deploy
```

The wizard creates an ignored deployment under `runtime/deployments/`, writes
owner-only credentials, starts the stack, and reports Grafana, InfluxDB, health,
and remaining collector setup.
The default bind address is `127.0.0.1`, so Grafana is initially available only
on the deployment host. Retrieve its generated login with
`.\itp.ps1 credentials grafana` on Windows or
`./itp credentials grafana` on macOS/Linux.

The examples below use the macOS/Linux launcher. On Windows PowerShell, replace
`./itp` with `.\itp.ps1`.

Useful commands:

```sh
./itp doctor
./itp status
./itp collector list
./itp collector add snmp
./itp collector test snmp
./itp dashboard generate
./itp restart
./itp logs
```

Use `./itp deploy --verbose` on macOS/Linux or
`.\itp.ps1 deploy --verbose` on Windows when diagnosing Docker build or startup
output.

## Supported collectors

SNMP, Juniper Mist, HPE Aruba Central, FortiGate, Palo Alto PAN-OS, PaperCut MF,
VMware vSphere, Microsoft Hyper-V, and Proxmox VE.

## Repository and runtime boundary

Source contains code, generic templates, dashboards, fictional examples, tests,
and documentation. Deployment configuration, secrets, generated dashboards,
logs, evidence, and persistent state live only under the ignored `runtime/`
tree.

## Documentation

- [macOS installation](docs/INSTALL_MACOS.md)
- [Linux installation](docs/INSTALL_LINUX.md)
- [Windows installation](docs/INSTALL_WINDOWS.md)
- [deployment runtime](docs/DEPLOYMENT_RUNTIME.md)
- [collector onboarding](docs/COLLECTOR_ONBOARDING.md)
- [security and secrets](docs/SECURITY_AND_SECRETS.md)
- [upgrades](docs/UPGRADE.md)
- [architecture](docs/architecture.md)
- [telemetry hardening contract](docs/telemetry-hardening.md)

ITP is Alpha software. Validate collectors and backup runtime data before
production use. See [LICENSE](LICENSE) and [CONTRIBUTING.md](CONTRIBUTING.md).

For contributor work, the developer bootstrap creates or updates `.venv` and
installs the `dev` dependency group declared by `pyproject.toml`.

Windows PowerShell:

```powershell
py scripts\bootstrap-dev.py
.\.venv\Scripts\Activate.ps1
python -m pytest
```

Use `python scripts\bootstrap-dev.py` if the Windows Python launcher is
unavailable.

macOS/Linux:

```sh
python3 scripts/bootstrap-dev.py
source .venv/bin/activate
python -m pytest
```

The POSIX fresh-clone validator requires a POSIX shell; Windows contributors
can run it from Git Bash or WSL. See [CONTRIBUTING.md](CONTRIBUTING.md).
