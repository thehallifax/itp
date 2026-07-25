# Install ITP

Requirements: Git, Python 3.9 or later, Docker Engine or Docker Desktop, Docker
Compose v2, and ports 3000 and 8181 available.

```sh
git clone <repository-url> itp
cd itp
cp .env.example .env
cp discovery/config.example.yml discovery/config.yml
```

Set unique `INFLUXDB_NODE_ID`, database/token settings, and Grafana password in `.env`.
Keep collectors disabled until their optional secret file is populated. For example:

```sh
cp secrets/mist.env.example secrets/mist.env
cp secrets/fortigate.env.example secrets/fortigate.env
```

Start and validate:

```sh
docker compose up -d --build
docker compose ps
docker compose run --rm collector python -m collectors validate
```

Grafana is available on `http://localhost:${GRAFANA_PORT:-3000}` and InfluxDB on port
8181. Dashboards, folders, and the FlightSQL datasource are provisioned automatically.
Alternatively run `./scripts/install.sh` or `./scripts/Install-ITP.ps1`.

On Windows PowerShell, the self-bootstrapping path is:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
.\itp.ps1 setup
.\itp.ps1 start
```

For one process without changing the persistent policy:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 setup
```

Use `pwsh` in place of `powershell.exe` for PowerShell 7. Organisation-enforced
Group Policy cannot be bypassed by ITP and may require administrator approval.

See [Platform prerequisites](platform-prerequisites.md) for Windows firmware
virtualization and optional-feature checks, macOS architecture guidance, Linux
Docker permissions, and verification commands.
