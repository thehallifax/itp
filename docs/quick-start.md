# ITP Quick Start

This guide prepares a new root Docker Compose deployment. Use deployment
profiles when multiple isolated customer estates must run on the same host.

## Requirements

- Docker Desktop or Docker Engine
- Docker Compose v2 (`docker compose version`)
- Available TCP ports 3000 and 8181 by default

Clone ITP and run the bootstrap wizard:

```sh
git clone https://github.com/thehallifax/itp.git
cd itp
./itp setup
```

On Windows:

```powershell
py scripts/itp.py setup
```

The wizard asks for the deployment name, deployment type, and Grafana port. It
then:

1. Verifies Docker and Docker Compose.
2. Checks the Grafana and InfluxDB ports.
3. Creates `.env` and `discovery/config.yml` from tracked examples when absent.
4. Adds deployment metadata and the selected Grafana port.
5. Runs `docker compose config --quiet`.
6. Optionally starts the platform and waits for its services to become ready.

When startup completes, open the printed dashboard URL, normally
`http://localhost:3000`.

## Automated setup

All prompts can be replaced with command-line options:

```sh
./itp setup --non-interactive \
  --deployment-name "North Campus" \
  --deployment-type "School" \
  --grafana-port 3000 \
  --start
```

Without `--start`, setup validates the deployment and prints the command needed
to start it. `--health-timeout` changes the default 180-second startup wait.

## Safe reruns

Running setup again does not overwrite `.env` or `discovery/config.yml`.
Interactive mode asks before updating the fields owned by the wizard. For
automation, `--force` explicitly permits updates to:

- `GRAFANA_PORT` in `.env`
- `deployment.name` and `deployment.type` in `discovery/config.yml`
- `customer` and `site` slugs derived from the deployment name

Other environment variables and configuration sections are retained.

## Enable collectors

The wizard does not request or store credentials. After setup:

1. Copy the required vendor file from `secrets/*.env.example` to its matching
   ignored `.env` file.
2. Add credentials only to that local secret file.
3. Set the collector's `enabled` value in `discovery/config.yml`.
4. Restart the collector service:

   ```sh
   docker compose restart collector
   ```

Never commit `.env`, `discovery/config.yml`, populated files under `secrets/`,
or generated files under `runtime/`.

Inspect all implemented connector boundaries before manual onboarding:

```sh
python -m collectors connectors list
python -m collectors connectors inspect mist
```

OOBE-001 bootstraps the platform. The registry introduced by OOBE-002 supplies
future onboarding metadata, but Phase 1 does not prompt for vendor credentials.
Connectors marked manual or profile-only are deliberately not presented as
guided options.

For multi-customer deployments, continue with
[deployment profiles](deployment-profiles.md).
