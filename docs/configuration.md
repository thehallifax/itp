# Configuration and credential resolution

ITP uses one deterministic resolver for connector readiness and provenance.
Tracked YAML contains anonymised, non-secret defaults. Ignored local files hold
deployment settings and credentials.

## Locations

Root deployment:

- `discovery/config.example.yml`: tracked defaults and schema
- `discovery/config.yml`: ignored deployment configuration
- `config/connectors.local.yml`: ignored non-secret connector overrides
- `.env` and `secrets/*.env`: ignored deployment environment and credentials

Profile deployment:

- `profiles/<profile>/discovery.yml`: tracked non-secret configuration
- `profiles/<profile>/connectors.local.yml`: ignored non-secret overrides
- `profiles/<profile>/.env` and `secrets/<profile>/*.env`: ignored credentials

Copy `config/connectors.local.example.yml` or
`profiles/connectors.local.example.yml`; never put credentials in connector
YAML.

## Current configuration inventory

| Boundary | Enabled and non-secret settings | Credential source |
| --- | --- | --- |
| SNMP | discovery YAML: networks, exclusions, version and intervals | `NETWORK_SNMP_COMMUNITY` |
| Mist | discovery YAML: endpoint, site and intervals | `MIST_ORG_ID`, `MIST_API_TOKEN` |
| FortiGate | discovery/local YAML: host, site, TLS and intervals | `FORTIGATE_API_TOKEN`; legacy host environment remains supported by the deployment template |
| Palo Alto | discovery/local YAML: endpoint, site, TLS, collection options and intervals | `PALOALTO_API_KEY` |
| PaperCut MF | discovery/local YAML: endpoint, site, TLS, thresholds and intervals | `PAPERCUT_AUTHORIZATION_KEY` |
| VMware | profile virtualisation endpoint YAML | profile `VMWARE_USERNAME`, `VMWARE_PASSWORD` |
| Hyper-V | profile virtualisation endpoint YAML and local PowerShell transport | no API credential in the connector registry |
| Proxmox | profile virtualisation endpoint YAML | profile `PROXMOX_TOKEN_ID`, `PROXMOX_TOKEN_SECRET` |
| InfluxDB | root/profile deployment settings: database, organisation and endpoint | `INFLUXDB_TOKEN` in the selected deployment secret file |
| Grafana | root/profile deployment settings: port and provisioning paths | administrator credentials in the selected ignored environment file |

UniFi, Aruba-specific APIs, and standalone printer APIs are not registered
connectors. Their current evidence is collected through SNMP or another
registered boundary, so they have no independent credential resolution path.
Connector enablement always comes from the selected discovery YAML or its
`connectors.local.yml` override.

## Precedence

For a selected deployment, the narrowest source wins:

1. Explicit process environment
2. Profile ignored environment and secret files
3. Profile `connectors.local.yml`
4. Tracked profile connector configuration
5. Root ignored environment and secret files
6. Root `config/connectors.local.yml` and deployment configuration
7. Tracked root defaults
8. Explicit connector default
9. A clear missing-configuration result

Profile selection itself is an explicit CLI boundary and supplies profile
identity, paths, database, and Compose project values. The resolver never
searches outside the documented files. Docker receives the selected files
through explicit `env_file`, environment, and bind-mount declarations; the
Python resolver reports the same effective connector readiness.

## Validation and provenance

Run:

```sh
./itp config validate
./itp config validate --json
./itp profile validate <profile>
```

Disabled connectors do not require credentials. Enabled connectors return a
non-zero validation result when mandatory credentials are absent. Diagnostics
show `configured` or `missing`, the source category, whether a setting is
secret, TLS policy, and site association. Values of secret settings are never
included.

`PAPERCUT_AUTHORIZATION_KEY` is canonical. The legacy
`PAPERCUT_AUTHORIZATION` name remains temporarily supported and emits a
value-free deprecation warning. The HTTP request behavior is unchanged.

## Rotation and backup

Rotate a credential by replacing it in the applicable ignored secret file,
then restart only that deployment. Back up `.env`, `config/*.local.yml`,
`profiles/*/*.local.yml`, `profiles/*/.env`, and populated `secrets/**/*.env`
outside Git using access-controlled storage.

Practical pre-review checks:

```sh
git status --short
git ls-files '.env' 'secrets/*.env' 'secrets/**/*.env' \
  'config/*.local.yml' 'profiles/*/*.local.yml'
git diff --check
```

Review logs and JSON diagnostics for setting status and provenance only; secret
values must never appear.

## Direct process-environment access

Process environment access is permitted only at bootstrap and runtime
boundaries: `collectors/config.py`, `collectors/configuration.py`,
`collectors/__main__.py`, profile activation, CLI/bootstrap entrypoints, and
analysis components that select runtime paths or deployment identity.

Connector business modules must not read the process environment. Credentials,
TLS policy, endpoints, enablement, and intervals are resolved at the
configuration boundary from registry metadata, then passed to collectors as
configuration objects. The same boundary supplies the Influx writer settings.
This keeps precedence, compatibility aliases, provenance, typed parsing, and
redaction consistent and prevents individual connectors from inventing lookup
orders. New connectors must declare credential and non-secret configuration
metadata in `collectors/connector-registry.yml` and consume only the resolved
configuration object.
