# Deployment profiles

Capability manifests are profile-scoped and carry canonical deployment,
customer and site IDs. They contain no credentials or endpoints.

Profiles authoritatively define deployment, customer and site identity as well
as the [deployment mode](deployment-modes.md). For example, the example-school reference
resolves to deployment `example-school`, customer `example-school` and site `site:example-school`; the friendly
site name may come from the ignored local metadata overlay.

An ITP profile is one independently operated customer deployment. Profiles share
application code but never runtime state, secrets, site aliases, containers,
volumes, dashboards, or telemetry databases.

## Layout

Tracked configuration lives in `profiles/<id>/`: `profile.yml`, `discovery.yml`,
`sites.yml`, `dashboards.yml`, and `env.example`. Credentials live in ignored
`secrets/<id>/*.env`; generated state lives in ignored `runtime/<id>/`.
Profile IDs are stable lowercase identifiers. Customer display names and
canonical site names remain separate.

The canonical development baseline is the anonymised `example-school` profile.
See [Deployment runtime](DEPLOYMENT_RUNTIME.md) for the profile, runtime,
database, collector, analysis, and dashboard lifecycle.

Tracked profile metadata is anonymised. Copy `sites.yml` to the ignored
`sites.local.yml` beside it to supply deployment-specific display names and
aliases. Profile activation automatically mounts that complete local registry
when present; otherwise it uses the tracked anonymised registry. Canonical
`site:<id>` values must remain unchanged. See the
[Canonical Site Registry](site-registry.md) for regeneration commands.

Connector settings and credentials follow the same deterministic local-file
model. See [Configuration and credential resolution](configuration.md) for
precedence, validation, rotation, and backup requirements.

## Quick start

```sh
./itp profile list
./itp profile init-secrets example-school
# Edit secrets/example-school/*.env without changing variable names.
./itp profile validate example-school
./itp profile up example-school
./itp profile status example-school
```

Operational commands include:

```sh
./itp profile down example-school
./itp profile restart example-school
./itp profile logs example-school
./itp profile shell example-school
./itp profile collect example-school paloalto
./itp profile dashboards example-school
./itp profile services example-school
```

The direct framework equivalents are:

```sh
python -m collectors --profile example-school validate
python -m collectors --profile example-school run
python -m collectors --profile example-school dashboards generate
python -m collectors --profile example-school services generate
```

No arbitrary profile is selected automatically. `ITP_PROFILE=example-school` is supported
for direct Compose use, but `./itp` is preferred because it resolves and prints
the selected paths.

## Secrets

Only enabled collectors require credentials. Validation detects missing, empty,
and common placeholder values without printing values. `init-secrets` creates
missing files from `.env.example` templates and never overwrites existing files.
Vendor credentials belong only in `secrets/<id>/`.

## Isolation model

Identity and presentation are separate. `sites.local.yml` may change names and
aliases only; changing its site ID set fails profile validation. Lifecycle
commands resolve `standalone` or `cluster_member` explicitly and never attach to
the other mode implicitly. See [Canonical identity](canonical-identity.md).

Each profile uses Compose project `itp-<id>`, profile-scoped containers, network,
InfluxDB and Grafana volumes, host ports, telemetry database, runtime directory,
site registry, and managed-dashboard output. Dashboard UIDs remain stable because
each profile has a separate Grafana instance.

Every native and Telegraf point includes `deployment_id`; canonical `site_id`
remains independent. Separate databases are the primary isolation boundary.
example-school defaults to ports 3000/8181 and example-corporate to 3100/8281, allowing concurrent stacks.

## Creating a profile

```sh
./itp profile create customer-id
./itp profile init-secrets customer-id
```

Creation uses generic placeholders and a disabled-collector baseline, and refuses
to overwrite an existing profile. Configure the customer name, canonical sites,
ports, scopes, collectors and credentials before validation.

## Legacy migration

The legacy `discovery/config.yml`, `config/sites.yml`, global `runtime/`, and
global `secrets/*.env` remain a deprecated compatibility mode only when no
profile is selected. They are not a second source of truth for profile
deployments. Existing untagged telemetry remains readable in its legacy
database; new profiles use their own database and add `deployment_id`.

## Updates, rollback and backup

```sh
git pull --ff-only
./itp profile validate example-school
./itp profile restart example-school
./itp profile status example-school
```

Repeat for every profile. Back up `profiles/<id>/`, `secrets/<id>/`,
`runtime/<id>/`, and Compose volumes bearing the profile project prefix. Roll
back by restoring the last known-good code and only that profile’s volumes and
runtime backup. Never restore one customer’s state into another profile.

`profile status` reports paths, containers, collectors, assets, services,
dashboard count and endpoints. `profile logs` provides diagnostics without
printing secret values.

## Virtualisation endpoints

Virtualisation is optional. Each endpoint has a stable ID, provider, canonical
site, TLS/transport policy and profile-scoped secret file. Multiple providers
may serve different sites in one profile. Static validation checks these
references and thresholds without requiring credentials. See
`config/examples/virtualisation.yml`; never point a profile at another
customer's management endpoint.

Run fixture integration without live endpoints, then generate the same
profile-scoped Operations and Service Health outputs:

```bash
./itp profile virtualisation <profile> --fixture vmware
ITP_PROFILE=<profile> ITP_RUNTIME_DIR="$PWD/runtime/<profile>" \
  .venv/bin/python -m collectors operations generate
ITP_PROFILE=<profile> ITP_RUNTIME_DIR="$PWD/runtime/<profile>" \
  .venv/bin/python -m collectors services generate
```

Fixture output remains under `virtualisation/fixtures/` and is intentionally not
read by live Operations generation.
