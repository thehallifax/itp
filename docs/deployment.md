# Deployment

## Clone to running

Prerequisites are Git, Docker Desktop or Docker Engine, and Docker Compose v2.

```sh
git clone <repository>
cd <repository>
./itp setup
./itp doctor
./itp start
./itp status
```

Setup creates local configuration from tracked examples and offers to provision
and start the stack. If declined, it prints `./itp start` as the next command.

## Lifecycle commands

```sh
./itp start [--json]
./itp stop [--json]
./itp restart [--json]
./itp logs [--follow] [--service <service>] [--tail <lines>]
```

Commands wrap the repository's existing Compose file. Repeated start and stop
operations are safe. Lifecycle output reports the resulting service state and
never prints environment values or credentials.

## Troubleshooting

- Run `./itp doctor --offline` for filesystem/configuration checks.
- Start Docker if the CLI reports that its daemon is unavailable.
- Run `./itp status --json` for Compose, InfluxDB, Grafana, daemon, and
  provisioning state.
- Use `./itp logs --service <service> --tail 200`.
- Rerun `./itp start` to recover partial provisioning safely.

Profile-scoped Compose commands remain available for existing multi-customer
deployments.
