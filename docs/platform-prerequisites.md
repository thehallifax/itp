# Platform prerequisites

ITP supports Python 3.9 or later. The launchers create `.venv` and install the
tracked runtime dependencies automatically; do not activate the environment or
install packages globally.

Contributors use `python3 scripts/bootstrap-dev.py`, which adds the tracked
development dependency group to the same environment. Activate `.venv` only
for contributor commands such as pytest. Runtime deployments do not install
development-only packages.

## Windows

Install:

- Git for Windows
- Python 3.9 or later from
  [python.org](https://www.python.org/downloads/windows/)
- Docker Desktop with Docker Compose v2
- Windows PowerShell 5.1 or PowerShell 7

During Python installation, enable `Add python.exe to PATH` and install the
Python launcher when offered. ITP tries `py -3`, then `python`, and finally
`python3`. Disable the Windows App Execution Aliases for `python.exe` and
`python3.exe` if they redirect to the Microsoft Store.

Verify:

```powershell
py -3 --version
git --version
docker --version
docker compose version
docker info
```

Windows may block local scripts. The recommended per-user policy is:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

For one invocation without a persistent policy change:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 --help
```

Use `pwsh` instead of `powershell.exe` for PowerShell 7. ITP cannot bypass a
policy enforced by organisational Group Policy.

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
