# Install ITP

Requirements: Git, Python 3.9 or later, Docker Engine or Docker Desktop, and
Docker Compose v2.

```sh
git clone <repository-url> itp
cd itp
./itp setup
```

Setup recommends available host ports, writes a stable deployment identity and
complete InfluxDB settings, and leaves every external collector disabled. Keep
collectors disabled until their optional secret file is populated. For example:

```sh
cp secrets/mist.env.example secrets/mist.env
cp secrets/fortigate.env.example secrets/fortigate.env
```

If setup was completed without starting services:

```sh
./itp start
./itp doctor
./itp status
```

Grafana and InfluxDB use the URLs printed by setup. Container ports remain 3000
and 8181 while `GRAFANA_PORT` and `INFLUXDB_PORT` select host-published ports.
Dashboards, folders, and the FlightSQL datasource are provisioned automatically
against the configured `INFLUXDB_BUCKET`.
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
