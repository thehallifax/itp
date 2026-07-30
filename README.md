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
the shorter `.\itp.ps1 deploy` command is equivalent. When Python is absent,
an interactive deployment can install it with consent through WinGet or a
SHA-256 and Authenticode-verified Python Software Foundation installer.

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
During an interactive deployment, choose the recommended generated Grafana
password or enter and confirm a custom password of at least 12 characters.
The generated password is shown once in the successful deployment summary and
is otherwise read from the deployment's protected runtime environment by the
credentials command.

The examples below use the macOS/Linux launcher. On Windows PowerShell, replace
`./itp` with `.\itp.ps1`.

Useful commands:

```sh
./itp collector list
./itp collector add <collector>
./itp collector test <collector>
./itp collect
./itp doctor
./itp status
./itp dashboard generate
./itp restart
./itp logs collector
```

Runtime commands infer the active deployment. Select one explicitly with
`--deployment <deployment-id>`, or make it active with
`./itp deployment select <deployment-id>`. Connector implementations run in
the shared `collector` service; use `./itp logs collector` for connector
runtime diagnostics.

Dashboard generation reports the managed dashboards and folders it refreshed.
Grafana polls the generated provisioning tree automatically; a restart is not
normally required.

Use `./itp deploy --verbose` on macOS/Linux or
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 deploy --verbose`
on Windows when diagnosing Docker build or startup
output.

Windows prerequisite diagnostics are read-only:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 prerequisites
```

On Windows, deployment can enable WSL and Virtual Machine Platform after
explicit consent. Feature changes require administrator approval and usually a
restart. ITP resumes automatically after the restart; Docker Desktop itself
must still be installed and started by the operator. Add `--json` to the
prerequisite command for structured output.

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
- [capability lifecycle](docs/collector-capabilities.md)
- [dashboard model](docs/dashboard-platform.md)
- [troubleshooting](docs/TROUBLESHOOTING.md)
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
