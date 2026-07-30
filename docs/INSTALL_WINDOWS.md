# Install ITP on Windows

ITP supports Windows 10 and Windows 11 releases that are supported by the
installed Docker Desktop release. Use Docker Desktop with Linux containers;
Windows containers are not supported.

## Prerequisites

Install:

- Git for Windows;
- Docker Desktop with Docker Compose v2.

Docker Desktop commonly uses its WSL 2 backend. WSL 2 must be installed and
enabled when that backend is selected, but ITP itself can be deployed directly
from PowerShell and does not require an interactive WSL shell. A supported
Hyper-V backend may also be used where Docker Desktop and the Windows edition
provide it.

Python does not need to be installed manually before an interactive
deployment. ITP tries `py -3`, then `python`, then `python3`, and validates
that the resolved interpreter is Python 3.9 or later. If none is supported,
the launcher uses this provider order:

1. exact WinGet package `Python.Python.3.12`, when WinGet is available;
2. pinned CPython 3.12.10 installer from `www.python.org`;
3. manual instructions if installation is declined or safely blocked.

Each automated provider requires consent. Pressing Enter accepts the default
Yes; declining makes no installation changes. The python.org fallback does not
depend on WinGet, Microsoft Store, Desktop App Installer, Chocolatey, Scoop, or
administrator rights.

Start Docker Desktop and verify the Linux-container engine before cloning ITP:

```powershell
git --version
docker version
docker compose version
docker info
```

WinGet may be unavailable on unregistered first-login environments,
stripped-down installations, or managed systems without App Installer. ITP
then offers the verified official installer directly. If `python.exe` opens
the Microsoft Store, ITP ignores it unless it resolves to a supported
interpreter.

## Clean installation

Open PowerShell and run:

```powershell
git clone https://github.com/thehallifax/infrastructure-telemetry-platform.git
cd infrastructure-telemetry-platform
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 deploy
```

The execution-policy override applies only to this launched PowerShell process
and does not permanently change machine or user policy. When local script
execution is already allowed, use the shorter command:

```powershell
.\itp.ps1 deploy
```

The PowerShell launcher invokes the shared bootstrap automatically. It creates
`.venv`, installs the runtime dependencies declared by `pyproject.toml`, checks
Docker and Compose, and starts the deployment. Running
`py scripts\bootstrap.py` separately is not required.

If Python is missing, installation never starts without consent. WinGet remains
the preferred provider. If it is unavailable or fails, ITP identifies CPython
3.12.10, the native architecture, and `www.python.org` before asking again.

The direct installer is pinned for AMD64 and ARM64. ITP downloads it over
validated HTTPS into a unique user temporary directory, rejects redirects away
from `www.python.org`, verifies the pinned SHA-256, and requires Windows
Authenticode status `Valid` with a Python Software Foundation signer. The
installer is then run in passive per-user mode with Python, pip, the launcher,
and user PATH integration enabled. The temporary installer is removed after
success or failure.

After either provider succeeds, ITP refreshes the current process from Machine
and User `PATH`, checks the expected per-user installation and launcher
locations, validates the interpreter and pip, and continues deployment without
requiring a new PowerShell session where possible.

Non-interactive invocations never prompt, download, or install software.
Preinstall Python 3.9 or later before using such an invocation. If an installer
reports success but Python cannot be resolved safely, ITP lists the locations
checked, exits non-zero, and provides the exact rerun command.

## Prerequisite diagnostics

Run the read-only diagnostic before deployment or when enterprise controls are
suspected:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 prerequisites
```

It reports Windows and PowerShell versions, native and process architecture,
execution-policy scopes, Git, Docker, Compose, daemon reachability, Python,
launcher, pip, WinGet, Desktop App Installer, the direct installer available
for the architecture, reachability of its exact python.org endpoint, and
whether missing prerequisites are blocking or automatically repairable. It
does not download or install anything.

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

The repository launcher may be blocked when local scripts are not permitted.
Use the canonical process-only invocation:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 deploy
```

Use `pwsh` instead of `powershell.exe` when using PowerShell 7. An execution
policy enforced by organisational Group Policy may prevent even the
process-level override; ITP cannot bypass that control, so contact the system
administrator.

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
- Automatic Python installation requires interactive consent.
- AppLocker, WDAC, Group Policy, endpoint protection, authenticated proxies,
  TLS inspection, or firewall rules may block download, trust validation, or
  installer execution. ITP does not bypass those enterprise controls.

See [Platform prerequisites](platform-prerequisites.md) and
[Deployment runtime](DEPLOYMENT_RUNTIME.md) for further diagnostics and
deployment behavior.
