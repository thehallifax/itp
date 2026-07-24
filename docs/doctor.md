# ITP Doctor

`doctor` is a read-only diagnostic report for local platform readiness,
services, connector configuration, state history, and the Operations Engine.
It never repairs files, changes credentials, restarts containers, enables
connectors, or scans networks.

## Usage

```sh
./itp doctor
./itp doctor --offline
./itp doctor --json
./itp doctor --platform-only
./itp doctor --connectors-only
./itp doctor --connector pan-os --offline
python -m collectors doctor --offline
```

`--offline` performs local checks and explicitly skips Docker daemon,
container, HTTP, and live connector validation. `--strict` treats warnings and
unavailable capabilities as failures for automation. Skipped and unsupported
checks are never reported as passed.

## Status and exit codes

Statuses are `pass`, `warn`, `fail`, `skip`, and `unavailable`. Severities are
`info`, `warning`, and `error`.

| Exit | Meaning |
| --- | --- |
| 0 | No failures; warnings allowed outside strict mode |
| 1 | Failure, or warning/unavailable under `--strict` |
| 2 | Invalid usage or unknown connector |
| 3 | Doctor failed before producing a report |

JSON includes schema version, timestamp, deployment identity, flags, overall
status, exit-code meaning, counts, ordered checks, and isolated errors.

## Secret safety

Doctor reports only whether credential variable names are present. It never
prints environment values or secret-file contents. Central redaction removes
known secrets and token/password/secret/community assignments from output,
exception details, remediation, commands, and JSON.

## Connector limitations

Requirements come from the shared connector registry. Existing connectors do
not yet declare dedicated doctor adapters, so that capability is shown as
`unavailable`. Live validation runs only with an explicitly supplied safe
adapter, declared validation support, complete configuration, and online mode.
SNMP Doctor never performs discovery. Profile-only virtualisation providers
await profile-aware adapters.

## Troubleshooting workflow

1. Run `./itp doctor --offline`.
2. Correct failed local configuration or missing tracked files.
3. Inspect the selected connector's documentation.
4. Run `./itp doctor` for Docker and local HTTP checks.
5. Use `--json --strict` in support automation.

Phase 1 reuses profile loading, connector metadata, YAML contracts,
state-history files, Compose definitions, and module boundaries. Vendor probes,
rule execution, repair, and container actions are deferred until safe read-only
adapters exist.
