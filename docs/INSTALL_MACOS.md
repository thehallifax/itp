# Install on macOS

Install Docker Desktop and Python 3.9 or later. Confirm Docker Desktop is
running, then:

```bash
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

Accept the detected timezone or enter an IANA timezone. The default listening
address is `127.0.0.1`; choose a LAN address only when remote access is required
and protected by the host firewall.

After deployment:

```bash
./itp doctor
./itp status
./itp collector list
```

Docker images must support the Mac architecture. Doctor reports an actionable
failure when Docker or the daemon is unavailable.
