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

Use a user-owned repository directory such as `C:\ITP`. The launcher blocks
protected locations including the Windows directory, System32, Program Files,
and ProgramData before configuration or containers are created. Downloads,
Desktop, OneDrive, and temporary directories produce a warning because
synchronisation and cleanup can disrupt container bind mounts.

Timezone values use IANA names. For Western Australia, enter
`Australia/Perth`.

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

## Windows platform preparation

After Python bootstrap and before Docker validation, ITP inspects:

- CPU and firmware virtualization;
- Windows Subsystem for Linux;
- WSL version, kernel, and default version where available;
- Virtual Machine Platform, Hyper-V, and Windows Hypervisor Platform;
- pending Windows restart state;
- Docker Desktop, CLI, daemon, Compose v2, and detected backend.

Virtualisation classification is architecture-aware. On ARM64, legacy
`Win32_Processor` firmware and SLAT fields are diagnostic evidence only and
cannot override a working Windows hypervisor, WSL2, VBS, or Docker daemon.
Unknown firmware state is reported as a warning; deployment is blocked only
when hardware virtualisation is positively confirmed unavailable without
contradictory operational evidence.

If WSL or Virtual Machine Platform is missing, an interactive deployment lists
the required features and asks `[Y/n]`. No feature is changed without consent.
After consent, ITP runs Microsoft's supported:

```powershell
wsl.exe --install --no-distribution
```

through an administrator approval prompt. It does not install a Linux
distribution or Docker Desktop. Group Policy or enterprise controls may block
the elevation or feature change.

Feature preparation requires a Windows restart. ITP exits cleanly and prints:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 deploy
```

The launcher returns Windows restart-required exit code `3010`; it does not
report deployment success before the post-restart run completes.

After restarting, the launcher detects that Python and the Windows features
are already ready and continues directly to Docker validation. It does not
repeat completed installation or feature work.

Docker Desktop installation is deliberately manual. If it is absent, install
Docker Desktop with Linux containers and rerun deployment. If it is installed
but stopped, start it and wait for the daemon before rerunning.

### Recover an interrupted InfluxDB credential bootstrap

ITP persists the generated `_admin` token atomically in the deployment runtime
before starting the remaining services. A normal rerun reuses that token and
does not request another one.

If an older interrupted deployment created `_admin` without saving it, ITP
does not delete its InfluxDB volume. For an established deployment, use the
supported InfluxDB operator-token recovery process and update the generated
deployment environment.

Only for a confirmed disposable deployment with no required telemetry, run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 deploy --force --reset-influx --verbose
```

The command requires typing `RESET <deployment-id>` before it removes only
that deployment's InfluxDB volume. Reset is unavailable in non-interactive
mode.

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

Diagnostics are grouped into Platform, Virtualization, Windows Features,
Applications, System State, and Deployment. Structured output is available:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 prerequisites --json
```

The JSON contract includes `platform`, `windowsFeatures`, `virtualization`,
`docker`, `rebootRequired`, `repairableItems`, and `blockingItems`. Readiness
item arrays contain stable identifiers such as `windows_feature.wsl` and
`docker.desktop_missing`. The `virtualization` object includes
`nativeArchitecture`, `firmwareVirtualizationState`,
`firmwareVirtualizationRaw`, `firmwareEvidenceReliable`, `wsl2Operational`,
`dockerVirtualizationOperational`, `operationalEvidence`, and
`conflictingEvidence`.

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

These commands infer the active deployment. When more than one deployment
exists, use the canonical selector after the command or nested subcommand:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 doctor --deployment example
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 status --deployment example
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 collect --deployment example
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 collector run paloalto --deployment example
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 logs collector --deployment example
```

Set the default explicitly with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 deployment select example
```

During interactive deployment, the wizard offers to generate a secure Grafana
administrator password (recommended) or accept a masked custom password twice.
Custom passwords must contain at least 12 characters. A newly generated
password is shown once in the successful deployment summary. Retrieve the
stored value later with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 credentials grafana
```

Add `--json` for machine-readable credential output. Treat that output as a
secret and do not write it to logs. Non-interactive deployment generates and
stores the password without printing it.

The deployment wizard also prints the Grafana URL and confirms that managed
dashboards were generated. Grafana polls the generated provisioning files
automatically, so a restart is not normally required. To refresh them manually:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 dashboard generate
```

Services bind to `127.0.0.1` by default and are therefore reachable only from
the Windows host.

Enabled connector implementations, including Palo Alto and PaperCut, run
inside the shared `itp-<deployment>-collector-1` container. They are not
separate Docker services. Use `logs collector` for their process logs and
`collector run <connector>` for a connector-specific collection. Discovery
runs in `itp-<deployment>-discovery-1`; a deliberately disabled discovery
service does not mean the shared collector has failed. Other supported log
targets are `discovery`, `telegraf`, `grafana`, and `influxdb3-core`.

### Trusting a private connector CA

For an HTTPS service signed by an internal CA, export the issuing root and each
required intermediate as **Base-64 encoded X.509 (.CER)** files in the Windows
Certificate Export Wizard. A Base-64 export is PEM encoded; do not export a
private key or a PKCS#12/PFX file. Install each certificate for the deployment:

```powershell
.\itp.ps1 credentials ca add .\private-root.cer --deployment example
.\itp.ps1 credentials ca add .\private-intermediate.cer --deployment example
.\itp.ps1 credentials ca list --deployment example
.\itp.ps1 restart --deployment example
.\itp.ps1 collector test papercut --deployment example --json
```

Use the fingerprint shown by `list` to remove a certificate:

```powershell
.\itp.ps1 credentials ca remove <fingerprint> --deployment example
```

These files remain in the ignored deployment runtime, are mounted read-only
into the collector, and extend normal public certificate trust. ITP never
disables TLS verification or prints certificate contents.

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
- Windows feature preparation requires interactive consent, administrator
  approval, and a restart.
- Docker Desktop installation remains an operator action.
- AppLocker, WDAC, Group Policy, endpoint protection, authenticated proxies,
  TLS inspection, or firewall rules may block download, trust validation, or
  installer execution. ITP does not bypass those enterprise controls.

See [Platform prerequisites](platform-prerequisites.md) and
[Deployment runtime](DEPLOYMENT_RUNTIME.md) for further diagnostics and
deployment behavior.
