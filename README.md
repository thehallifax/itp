# Infrastructure Telemetry Platform

ITP turns infrastructure collectors into vendor-neutral inventory, telemetry,
service health, operational findings, notifications, and managed Grafana
dashboards.

Deployment identity, telemetry validation, collector execution health, and
dashboard scoping are framework-owned, so vendor site names cannot fragment
operational views.

## Deploy in one command

Install Docker Desktop or Docker Engine with Compose v2, then:

```bash
git clone https://github.com/thehallifax/infrastructure-telemetry-platform.git
cd infrastructure-telemetry-platform
./itp deploy
```

The wizard creates an ignored deployment under `runtime/deployments/`, writes
owner-only credentials, starts the stack, and reports Grafana, InfluxDB, health,
and remaining collector setup.
The default bind address is `127.0.0.1`, so Grafana is initially available only
on the deployment host. Retrieve its generated login with:

```bash
./itp credentials grafana
```

Useful commands:

```bash
./itp doctor
./itp status
./itp collector list
./itp collector add snmp
./itp collector test snmp
./itp dashboard generate
./itp restart
./itp logs
```

Use `./itp deploy --verbose` (or `ITP_VERBOSE=1 ./itp deploy`) when diagnosing
Docker build or startup output.

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
installs the tracked `dev` dependency group, including pytest:

```bash
python3 scripts/bootstrap-dev.py
source .venv/bin/activate
./itp config validate
./scripts/validate-fresh-clone.sh
python -m pytest -q
```

Windows contributors use `py -3 scripts/bootstrap-dev.py` followed by
`.\.venv\Scripts\Activate.ps1`. See [CONTRIBUTING.md](CONTRIBUTING.md).
