# Canonical Service Health Engine

Service evaluators consume the profile capability manifest. Unsupported
capabilities explain exclusions without degrading health; failed supported
evidence can degrade Monitoring and its related service.

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
- `runtime/deployments/<deployment>/generated/dashboard/managed/registry.json`
  for enabled collectors and merged
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

Service evaluation consumes only the latest canonical connector and capability
state. A recovered connector's historical failure remains in source-run and
PipelineRun audit history, but cannot keep a service Warning or Critical.
Current versus historical state and manual-run semantics are documented in
[Status and Health State](status-and-health.md).

`Unknown` remains the Service Health result for an enabled capability without
trustworthy service evidence. The dashboard readiness contract adds the
operator-facing distinction between first-run waiting and failed/stale
collection; service evaluators must not duplicate collector readiness policy.

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

## Projection contract

| Service | Evidence | Deterministic decision and explanation | Dashboard output |
| --- | --- | --- | --- |
| Internet | Authoritatively classified WAN signals | Critical when all configured uplinks are down; Warning for degraded redundancy; Healthy only with positive current uplink evidence | Internet card and per-WAN graphs |
| Security | Firewall assets/signals plus security, subscription and certificate findings | Highest explicit availability or security degradation; summary states whether availability, certificate, or subscription evidence requires attention | Security, Firewall and Certificates cards |
| Monitoring | Enabled collector records and collector findings | Critical/Warning for failed, stale or partial coverage; Healthy only with successful current collector evidence | Monitoring card and Collector Health |
| Printing | Printer/server assets and printing findings | Critical/Warning for unavailable services or actionable device conditions; otherwise Healthy with current PaperCut evidence | Printing card and printer exception table |
| Switching | Switch assets, availability and network findings | State follows authoritative switch availability and findings; no evidence remains Unknown | Switching card/detail |
| Wireless | AP/client assets and wireless findings | State follows authoritative AP/client evidence; absent capability is Not Enabled | Wireless card/detail |
| Identity | Identity service assets and findings | Explicit outage/degradation wins; no enabled capability is Not Enabled | Identity service card |
| Compute | Server/virtualisation assets and findings | Explicit workload/platform impact wins; powered-off alone is not failure | Compute service card |
| Storage | Storage assets and findings | Explicit unavailability/capacity evidence determines state | Storage service card |
| Voice | Voice assets and findings | Explicit service-impact evidence determines state | Voice service card |
| Email | Email assets and findings | Explicit service-impact evidence determines state | Email service card |

Every emitted row contains the selected status, human explanation, affected
assets/users where inferable, last change, and evidence. Dashboards render this
projection; they do not re-evaluate the decision.

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

When the `virtualisation` capability is enabled, six additional services are
instantiated: Virtualisation Management Plane, Hypervisor Cluster, Compute
Capacity, Virtual Machine Hosting, Shared Storage and Workload Availability.
They consume affected-service IDs on promoted findings. Powered-off workloads
are not failures by default, and manager loss cannot make hosting Critical
without separate impact evidence. Profiles without the capability retain the
original service list unchanged.
