# Install on Linux

Install Python 3.9 or later, Docker Engine, and the Docker Compose v2 plugin.
Allow the operator account to use Docker, then:

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

The default service binding is localhost. Use a specific management address
only with appropriate firewall and access controls.

Validate with:

```bash
./itp doctor
./itp status
docker compose version
```
