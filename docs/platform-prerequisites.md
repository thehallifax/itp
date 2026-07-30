# Platform prerequisites

ITP supports Python 3.9 or later. The launchers create `.venv` and install the
tracked runtime dependencies automatically; do not activate the environment or
install packages globally.

Contributors use `python3 scripts/bootstrap-dev.py`, which adds the tracked
development dependency group to the same environment. Activate `.venv` only
for contributor commands such as pytest. Runtime deployments do not install
development-only packages.

On Windows, use `py scripts\bootstrap-dev.py` and activate with
`.\.venv\Scripts\Activate.ps1`; use `python` instead of `py` only when the
Python launcher is unavailable.

## Windows

Install:

- Git for Windows
- Docker Desktop with Docker Compose v2
- Windows PowerShell 5.1 or PowerShell 7

ITP tries `py -3`, then `python`, and finally `python3`, validating Python 3.9
or later rather than relying on command presence. During an interactive
deployment, it can install the exact WinGet package `Python.Python.3.12` after
the user consents. It then refreshes the current Machine and User `PATH` and
continues without reopening PowerShell where the interpreter can be resolved.
Non-interactive invocations never prompt or install software.

Verify:

```powershell
git --version
docker --version
docker compose version
docker info
```

Automatic installation requires WinGet. It may be unavailable on older
Windows builds, unregistered first-login environments, stripped-down servers,
or managed systems where App Installer is unavailable. Install Python from
[python.org](https://www.python.org/downloads/windows/) and enable
`Add python.exe to PATH` when automatic installation is unavailable. Disable
Windows App Execution Aliases if they redirect `python.exe` or `python3.exe`
to the Microsoft Store.

Windows may block local scripts. The canonical first-run command uses a
process-only override without changing persistent machine or user policy:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 deploy
```

Use `.\itp.ps1 deploy` when scripts are already allowed, or `pwsh` instead of
`powershell.exe` for PowerShell 7. Organisational Group Policy may prevent the
process-level override; ITP cannot bypass that control.

### Docker virtualization

Docker Desktop requires hardware virtualization. Enable AMD SVM/AMD-V or Intel
VT-x in system firmware. Depending on the Windows edition and selected Docker
backend, Windows may also require:

- Virtual Machine Platform
- Windows Hypervisor Platform
- Hyper-V, where the edition supports it

Inspect feature state from an elevated PowerShell window:

```powershell
systeminfo.exe
Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform
Get-WindowsOptionalFeature -Online -FeatureName HypervisorPlatform
Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All
bcdedit /enum "{current}"
```

An unavailable optional feature does not necessarily indicate an error because
Windows editions expose different feature sets. Reboot after changing firmware,
Windows features, or hypervisor boot configuration. ITP performs read-only
diagnostics and never changes these settings.

## macOS

Install:

- Git
- Python 3.9 or later
- Docker Desktop with Docker Compose v2

Verify:

```sh
python3 --version
git --version
docker --version
docker compose version
docker info
```

Docker Desktop provides native Apple Silicon and Intel builds. Install the build
matching the Mac architecture. ITP itself uses platform-independent Python;
container image architecture support is resolved by Docker.

## Linux

Install:

- Git
- Python 3.9 or later, including the distribution's `venv` package
- Docker Engine
- Docker Compose v2 plugin

Verify:

```sh
python3 --version
git --version
docker --version
docker compose version
docker info
```

Run Docker as a user with access to the daemon. Distribution packages commonly
use the `docker` group; group membership changes require a new login session.
Do not make the Docker socket broadly writable.

## Bootstrap troubleshooting

Normal bootstrap output is intentionally concise. To include successful pip
output for troubleshooting:

```sh
./itp --verbose demo --help
```

```powershell
.\itp.ps1 --verbose demo --help
```

If installation fails, ITP always prints the captured pip diagnostics even
without verbose mode. The first installation requires access to Python packages
through the public package index, a configured internal index, or pip's cache.
