# Install ITP

ITP requires Git, Python 3.9 or later, Docker Desktop or Docker Engine, and
Docker Compose v2.

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

On Windows PowerShell, run `.\itp.ps1 deploy`. See
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
