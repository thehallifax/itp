# Install ITP on Windows

ITP supports Windows 10 and Windows 11 releases that are supported by the
installed Docker Desktop release. Use Docker Desktop with Linux containers;
Windows containers are not supported.

## Prerequisites

Install:

- Git for Windows;
- Python 3.9 or later from python.org;
- Docker Desktop with Docker Compose v2.

Docker Desktop commonly uses its WSL 2 backend. WSL 2 must be installed and
enabled when that backend is selected, but ITP itself can be deployed directly
from PowerShell and does not require an interactive WSL shell. A supported
Hyper-V backend may also be used where Docker Desktop and the Windows edition
provide it.

During Python installation, enable the Python launcher and add Python to
`PATH`. ITP tries `py -3`, then `python`, then `python3`.

Start Docker Desktop and verify the Linux-container engine before cloning ITP:

```powershell
py -3 --version
git --version
docker version
docker compose version
docker info
```

If `py` is unavailable, use `python --version`. If `python.exe` opens the
Microsoft Store, install Python from python.org and disable the conflicting
Windows App Execution Alias.

## Clean installation

Open PowerShell and run:

```powershell
git clone https://github.com/thehallifax/infrastructure-telemetry-platform.git
cd infrastructure-telemetry-platform
.\itp.ps1 deploy
```

The PowerShell launcher invokes the shared bootstrap automatically. It creates
`.venv`, installs the runtime dependencies declared by `pyproject.toml`, checks
Docker and Compose, and starts the deployment. Running
`py scripts\bootstrap.py` separately is not required.

Deployment configuration, generated credentials, dashboards, and local runtime
state are stored below:

```text
runtime\deployments\<deployment-id>\
```

External collectors remain disabled until configured.

## Verify and sign in

```powershell
.\itp.ps1 doctor
.\itp.ps1 status
.\itp.ps1 credentials grafana
```

The deployment wizard prints the Grafana URL. Services bind to `127.0.0.1` by
default and are therefore reachable only from the Windows host.

To permit remote access, explicitly choose an appropriate management address
during deployment and restrict inbound access with Windows Defender Firewall
or an equivalent network control. Do not expose Grafana or InfluxDB directly
to untrusted networks.

## PowerShell execution policy

The repository PowerShell launcher may be blocked when local scripts are not
permitted. A common per-user setting is:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

For a single invocation without changing the persistent policy:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 deploy
```

Use `pwsh` instead of `powershell.exe` when using PowerShell 7. An execution
policy enforced by organisational Group Policy requires administrator help.

## Contributor setup

```powershell
py scripts\bootstrap-dev.py
.\.venv\Scripts\Activate.ps1
python -m pytest
```

Use `python scripts\bootstrap-dev.py` if `py` is unavailable. The developer
bootstrap is idempotent and installs the declared `.[dev]` extra without
changing runtime dependency policy.

`scripts\validate-fresh-clone.sh` is a POSIX shell validator. Run it through
Git Bash or WSL when required; it is not a native PowerShell script.

## Stop or remove a deployment

Stop the active deployment and remove its containers and Compose network with:

```powershell
.\itp.ps1 stop
```

ITP intentionally has no automatic data-purge command. Docker volumes and the
ignored `runtime\` deployment files are retained for recovery. Back up any
required data before removing those resources manually with Docker Desktop and
the filesystem, then remove the cloned repository if it is no longer needed.

## Windows-specific limitations

- Docker Desktop must be running before stack commands.
- Only Linux containers are supported.
- WSL or Git Bash is required for POSIX-only maintenance scripts.
- Windows App Execution Aliases can interfere with Python discovery.
- Corporate execution policy, endpoint protection, proxy, or firewall rules
  may require administrator-approved configuration.

See [Platform prerequisites](platform-prerequisites.md) and
[Deployment runtime](DEPLOYMENT_RUNTIME.md) for further diagnostics and
deployment behavior.
