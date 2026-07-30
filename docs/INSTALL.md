# Install ITP

ITP requires Git, Docker Desktop or Docker Engine, Docker Compose v2, and
Python 3.9 or later. On an interactive Windows deployment, the launcher can
install Python with explicit consent. It prefers WinGet and otherwise uses a
pinned, SHA-256 and Authenticode-verified installer from `www.python.org`.
After Python is available, the Windows launcher checks WSL, Virtual Machine
Platform, virtualization, restart state, and Docker Desktop. It can enable the
required Windows features with consent but does not install Docker Desktop.

From a clean clone:

```sh
git clone https://github.com/<organisation>/<repository> infrastructure-telemetry-platform
cd infrastructure-telemetry-platform
./itp deploy
```

The deployment wizard validates prerequisites, creates ignored runtime
configuration, provisions Grafana, starts the stack, and prints the dashboard
URL. It does not enable external collectors or request vendor credentials.

After deployment:

```sh
./itp doctor
./itp status
./itp collector list
```

On Windows PowerShell, the canonical first-run command is
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 deploy`.
Use `.\itp.ps1 deploy` where script execution is already allowed. See
[Platform prerequisites](platform-prerequisites.md), the
[Windows guide](INSTALL_WINDOWS.md), [macOS guide](INSTALL_MACOS.md), or
[Linux guide](INSTALL_LINUX.md) for operating-system details.

## Contributor installation

The deployment launcher intentionally installs runtime packages only.
Contributors should use the deterministic developer bootstrap:

```sh
python3 scripts/bootstrap-dev.py
source .venv/bin/activate
./itp config validate
./scripts/validate-fresh-clone.sh
python -m pytest
```

It installs the `dev` extra from `pyproject.toml`, including pytest, and is safe
to rerun after dependency changes.

On Windows PowerShell:

```powershell
py scripts\bootstrap-dev.py
.\.venv\Scripts\Activate.ps1
python -m pytest
```

Use `python scripts\bootstrap-dev.py` when `py` is unavailable. The
fresh-clone shell validator requires Git Bash or WSL on Windows.
