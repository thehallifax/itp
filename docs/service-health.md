# Canonical Service Health Engine

The Service Health Engine is the vendor-neutral policy boundary between
canonical infrastructure data and operational dashboards. It evaluates
Internet, Wireless, Switching, Printing, Identity, Compute, Storage, Voice,
Email, Security, and Monitoring without referring to collector vendors.

## Inputs and flow

The engine reads:

- `runtime/infrastructure/state.json` for fused assets, collector states, and
  canonical signals.
- `runtime/operations/operations.json` for deterministic active issues and
  risks.
- `runtime/dashboard/managed/registry.json` for enabled collectors and merged
  capabilities.

Aliases are first resolved through the canonical registry in
`config/sites.yml`. Each evaluator then receives only the assets, findings,
signals, and collectors belonging to one site. The engine writes stable results
to:

- `runtime/services/service-health.json`
- `runtime/services/service-health.csv`

Run it manually with:

```sh
python -m collectors services generate
```

List the evaluator-to-capability contract with:

```sh
python -m collectors services evaluators
```

## State policy

- **Not Enabled** — the required capability is absent.
- **Unknown** — the capability is enabled but no trustworthy evidence exists.
- **Healthy** — positive asset, signal, or collector evidence exists without
  active degradation.
- **Warning** — deterministic warning evidence or a non-critical finding exists.
- **Critical** — an explicit outage, failed collector, offline service asset, or
  Critical/High active issue exists.

The engine never converts missing data into a healthy state. Every service row
also records its summary, severity, affected assets, inferable affected-user
count, last evidence change, and evidence list.

## Site and estate scopes

Schema version 2 contains:

- `sites[]`: one record per configured canonical `site_id`, with canonical
  `site_name`, overall status, relevant collectors, capabilities, and services.
- `estate`: the deterministic All Sites aggregate, evaluated once from complete
  input rather than constructed by concatenating site results.
- `diagnostics[]`: unmapped or ambiguous assets, findings, signals, and
  collectors that cannot be assigned safely.

CSV rows carry `scope`, `site_id`, and `site_name`. They are ordered by
canonical site ID and canonical service order.

Collector coverage is inferred from its canonical assets or explicit
`site_ids`. A collector marked `shared: true` applies to every configured site.
An unattributed collector remains estate-only and creates a diagnostic.
Per-site capabilities are the union supplied by collectors relevant to that
site.

## Capability mapping

| Service | Capability |
| --- | --- |
| Internet | `internet` |
| Wireless | `wireless` |
| Switching | `switching` |
| Printing | `printing` |
| Identity | `identity` |
| Compute | `compute` |
| Storage | `storage` |
| Voice | `voice` |
| Email | `email` |
| Security | `firewall` |
| Monitoring | `telemetry` |

Storage, Voice, and Email remain `Not Enabled` until a collector manifest
provides those capabilities. Security currently uses the existing firewall
capability because firewall collectors provide the available security evidence.

Internet evaluation consumes only signals marked
`classification_authoritative=true`. Unconfigured, invalid, incomplete, or
stale WAN evidence is Unknown. All configured uplinks down is Critical;
primary down with a working configured backup is Warning.

Security evaluation may use expired subscription and device-certificate
evidence. Content package age is informational unless deployment policy
explicitly defines warning and critical thresholds.

## Extension

Add a `ServiceDefinition` and registered evaluator in
`analysis/services/evaluators.py`. Select only canonical asset attributes,
operation categories/rule identifiers, and canonical signal names. Do not add
vendor-name conditions or dashboard code.

The Operations Wallboard consumes the per-site and estate records directly,
without duplicating evaluation policy.
