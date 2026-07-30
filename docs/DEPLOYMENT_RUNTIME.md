# Deployment runtime

Runtime status sources, recovery rules, and the separation between health and
freshness are defined in
[Status and Health State](status-and-health.md).

The collector container rebases tracked `/app/runtime/...` template paths onto
`ITP_RUNTIME_DIR=/app/runtime/<deployment>`. Each analysis cycle writes
inventory, infrastructure, operations, and service-health state before
regenerating platform dashboard snapshots under
`generated/dashboard/managed/`. This is the same deployment-owned tree mounted
into Grafana for file provisioning. Dashboard JSON is validated and atomically
replaced, so a render failure leaves the previous valid managed dashboard
available. Grafana checks managed folders every 30 seconds and loads the
replacement without UI edits.

Dashboard JSON and Grafana provisioning YAML cross the collector/Grafana
container boundary. They are deliberately published as `0644`, with shared
dashboard directories as `0755`, so Grafana UID 472 can traverse and read the
bind mount. Atomic replacement preserves those modes. This policy applies only
to non-secret dashboard artifacts. Deployment environments, connector secrets,
tokens, private keys, and CA material remain owner-only (`0600`, with secret
directories `0700` where supported).

`./itp start` and `./itp restart` rebuild the deployment images before starting
services. This ensures a source update cannot leave the scheduler running an
older collector image.

This is the authoritative deployment path for a new ITP installation.
Operating-system details are in the [macOS](INSTALL_MACOS.md) and
[Linux](INSTALL_LINUX.md) guides.

## Prerequisites

- Git
- Python 3.9 or later
- Docker Desktop or Docker Engine with a running daemon
- Docker Compose v2 (`docker compose version`)
- Free TCP ports for Grafana and InfluxDB (defaults: 3000 and 8181)

Docker and Compose release lifecycles vary by operating system. Use a
vendor-supported Docker release that provides Compose v2.

## Clean installation

```bash
git clone https://github.com/thehallifax/infrastructure-telemetry-platform.git
cd infrastructure-telemetry-platform
./itp deploy
```

`./itp deploy` verifies Docker, asks for deployment identity and ports, creates
ignored runtime configuration, generates credentials, provisions managed
Grafana dashboards, builds images, starts the stack, and prints the Grafana
and InfluxDB URLs. InfluxDB storage is initialised by the container; no manual
database step is required.
Routine Docker output is captured so the default deployment transcript stays
operator-focused. Use `./itp deploy --verbose` or set `ITP_VERBOSE=1` for full
build and Compose output.

For repeatable automation without enabling collectors:

```bash
./itp deploy \
  --non-interactive \
  --deployment-name "Example School" \
  --deployment-id example-school \
  --timezone UTC \
  --grafana-port 3000 \
  --influxdb-port 8181
```

The tracked `.env.example`, `discovery/config.example.yml`, and
`secrets/*.env.example` files document the legacy-compatible root configuration
contract. The deployment command does not copy or modify them. It writes the
active configuration beneath `runtime/deployments/<deployment-id>/`.

## Runtime layout

```text
runtime/deployments/<deployment-id>/
  deployment.yml
  collectors.yml
  dashboards.yml
  secrets/
  generated/
  logs/
  evidence/
  state/
runtime/shared/
runtime/backups/
```

`deployment.yml` owns identity, timezone, deployment mode, addresses, and
ports. `collectors.yml` owns enablement and non-secret connector configuration.
`generated/deployment.env` contains generated stack credentials and is
owner-readable only on macOS and Linux. Runtime files are ignored by Git.

Deployment metadata is authoritative for telemetry. It owns stable deployment,
customer and site IDs plus display name, timezone, region, and currency where
configured. Connectors cannot override these values.

## Verify health

```bash
./itp doctor
./itp status
./itp collector list
docker compose ls
```

Use `./itp deployment show --json` to inspect the resolved runtime paths when
directly inspecting a deployment. The CLI remains the preferred interface:

```bash
./itp logs --tail 200
./itp status --json
```

Grafana is available at the URL printed by deploy. InfluxDB and Grafana expose
container health checks; Doctor verifies platform configuration, Docker,
services, connector registration, and reachable health surfaces.

Doctor also checks canonical identity, runtime capabilities, telemetry schema,
scheduler health, dashboard provisioning, datasource configuration, and
service availability.

Display the active deployment's generated Grafana login without editing files:

```bash
./itp credentials grafana
```

Use `--deployment <deployment-id>` to select a deployment and `--json` for
machine-readable output. The command is read-only. During interactive creation,
the recommended generated password is displayed once in the successful
deployment summary; a custom password may instead be entered and confirmed
without terminal echo. Non-interactive creation generates and stores a password
without printing it. Credentials and all other runtime material remain under
the ignored `runtime/deployments/<deployment-id>/` directory.

## Deployment selection

Commands that operate on one runtime deployment accept:

```bash
--deployment <deployment-id>
```

Place the selector after the top-level command, or after a nested operational
subcommand:

```bash
./itp doctor --deployment example
./itp collect --deployment example
./itp collector run paloalto --deployment example
./itp dashboard generate --deployment example
./itp credentials grafana --deployment example
./itp logs collector --deployment example
```

Without the selector, ITP uses the active deployment. If the active marker is
absent and exactly one deployment exists, that deployment is inferred. Multiple
deployments without an active selection produce an error listing the available
IDs. Set the active deployment with:

```bash
./itp deployment select example
```

Repository-global commands such as `config validate`, connector metadata
inspection, setup, demo generation, and profile management do not use this
selector.

### Command contract

| Command | Subcommand(s) | Scope | `--deployment` | Omitted selector |
|---|---|---|---|---|
| `deploy`, `init` | — | Creates a deployment | No; use `--deployment-id` | Creates or preserves the requested ID and marks it active |
| `deployment` | `list`, `show`, `select` | Deployment catalogue | No; ID is positional | `show` uses active/sole deployment |
| `credentials` | `grafana` | One deployment | Yes | Active, then sole deployment |
| `collector` | `list`, `add`, `test`, `run`, `remove` | One deployment | Yes | Active, then sole deployment |
| `dashboard` | `generate` | One deployment | Yes | Active, then sole deployment |
| `update` | — | One deployment | Yes | Active, then sole deployment |
| `connectors` | `list`, `inspect` | Repository metadata | No | Not applicable |
| `doctor` | — | One deployment, with legacy root fallback | Yes | Active, sole, then legacy root when none exist |
| `collect` | — | One deployment, with legacy root fallback | Yes | Active, sole, then legacy root when none exist |
| `status` | — | One deployment, with legacy root fallback | Yes | Active, sole, then legacy root when none exist |
| `daemon` | — | One deployment, with legacy root fallback | Yes | Active, sole, then legacy root when none exist |
| `start`, `stop`, `restart` | — | One deployment, with legacy root fallback | Yes | Active, sole, then legacy root when none exist |
| `logs` | service name | One deployment, with legacy root fallback | Yes | Active, sole, then legacy root when none exist |
| `notifications` | `evaluate`, `list`, `inspect`, `test`, `acknowledge` | One deployment, with legacy root fallback | Yes | Active, sole, then legacy root when none exist |
| `setup`, `demo` | — | Repository/bootstrap utility | No | Not applicable |
| `config` | `validate` | Repository configuration | No | Not applicable |
| `profile` | profile lifecycle actions | Explicit profile | No; profile ID is positional | Never inferred |

Nested runtime commands accept the selector after their subcommand. The
documented canonical placement is the rightmost command-local form shown above.
The parent-command form remains accepted for compatibility where it already
existed.

## Configure discovery and collectors

Fresh deployments intentionally enable no external collectors and do not
contact infrastructure. Configure one through the registry-driven workflow:

```bash
./itp collector list
./itp collector add <collector>
./itp collector test <collector>
./itp dashboard generate
./itp restart
```

Dashboard generation lists the managed dashboards refreshed for the selected
deployment. Grafana polls the generated provisioning tree every 30 seconds, so
dashboard regeneration does not normally require a Grafana restart.

## Shared collector and diagnostics

`itp-<deployment>-collector-1` runs all enabled runtime connector
implementations. Palo Alto and PaperCut do not create containers named after
the connector. Run all independently eligible connectors once with:

```bash
./itp collect --deployment example
```

Run one enabled connector with:

```bash
./itp collector run paloalto --deployment example
```

An incomplete connector is skipped with its missing configuration or credential
identifiers while other configured connectors continue. Diagnostic output
never renders credential values. `status` reports connector configuration,
freshness, last run, last success or failure, record count, shared collector
service state, and the independent discovery state.

Collectors that cannot run in the selected placement report
`execution_mode_mismatch`, including the collector mode, deployment mode, and
safe remediation. Disabled, prerequisite, execution-mode, and runtime-policy
skips remain distinct. Palo Alto supports both central and edge placement
because it communicates directly with the PAN-OS management XML API.

### Private certificate authorities

HTTPS connectors use the operating system's public CA trust plus any
deployment-specific private roots or intermediates installed by the operator.
Certificates are stored only under the ignored
`runtime/deployments/<id>/secrets/ca/` directory and mounted read-only into
the shared collector. TLS verification remains enabled.

```bash
./itp credentials ca add ./private-root.pem --deployment example
./itp credentials ca add ./private-intermediate.pem --deployment example
./itp credentials ca list --deployment example
./itp restart --deployment example
```

Remove a certificate by its displayed name or an unambiguous fingerprint:

```bash
./itp credentials ca remove private-root --deployment example
```

The generated bundle extends, rather than replaces, Python's normal public CA
trust. Certificate bodies are never written to command output.

PaperCut also supports a connector-local `verify_tls: false` setting. This
does not affect other connectors, but it permits interception or server
impersonation and is intended only for trusted internal networks. Prefer
installing the private root and intermediate CA certificates.

PaperCut System Health collection uses
`GET /api/health?Authorization=<key>` with structured query construction and
`Accept: application/json`. The key is never rendered in diagnostics.
Redirects are reported rather than followed. A rejected request includes only
bounded, sanitized HTTP metadata in `collector test --json`; credential values,
cookies, and complete HTML responses are excluded.

Supported service log targets are:

```bash
./itp logs collector --deployment example
./itp logs discovery --deployment example
./itp logs telegraf --deployment example
./itp logs grafana --deployment example
./itp logs influxdb3-core --deployment example
```

The add command writes non-secret discovery/collector settings to
`collectors.yml` and credentials to the deployment's ignored `secrets/`
directory. Required placeholders fail through collector validation with an
actionable message. Optional fields and connector-specific prerequisites are
documented in [Collector onboarding](COLLECTOR_ONBOARDING.md).

## Stop, restart, and recover

```bash
./itp stop
./itp start
./itp restart
./itp status
```

Stopping removes containers but preserves named Docker volumes and the runtime
deployment. Starting again reuses both. No source file should change during
deployment or lifecycle operations.

## Backup and upgrade

Back up the complete `runtime/` directory and the deployment's named Docker
volumes before an upgrade. Runtime configuration alone does not contain
InfluxDB or Grafana volume contents.

```bash
./itp update
./itp doctor
./itp status
```

Update requires a clean source checkout, performs a fast-forward-only pull,
rebuilds images, and preserves the active runtime deployment. See
[Upgrade](UPGRADE.md) for rollback guidance.

## Secrets

Never commit `runtime/`, populated environment files, credentials, support
evidence, or database exports. Bind services to localhost unless remote access
is explicitly protected. See [Security and secrets](SECURITY_AND_SECRETS.md)
for storage, permissions, rotation, and disclosure guidance.
