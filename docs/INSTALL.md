# Install ITP

ITP requires Git, Python 3.9 or later, Docker Desktop or Docker Engine, and
Docker Compose v2.

From a clean clone:

```sh
git clone https://github.com/<organisation>/<repository> infrastructure-telemetry-platform
cd infrastructure-telemetry-platform
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
python -m pytest -q
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
[macOS guide](INSTALL_MACOS.md), or the [Linux guide](INSTALL_LINUX.md) for
operating-system details.
