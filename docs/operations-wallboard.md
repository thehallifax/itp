# Operations Wallboard

Operations Wallboard is a dense, no-scroll Grafana view for continuous display in
an IT office, NOC, school technology office, or MSP operations area. It is
optimised for 1920 × 1080 and complements—not replaces—the interactive
Infrastructure Overview dashboard.

## Display and kiosk mode

Open dashboard UID `itp-operations-wallboard`, select All Sites or one canonical
site, then use Grafana kiosk mode:

```text
/d/itp-operations-wallboard?orgId=1&kiosk
```

The dashboard uses 27 grid rows, has no collapsible sections, and keeps all major
zones in the initial 16:9 viewport. At smaller resolutions Grafana may reduce text
density.

## Data and filtering

The renderer consumes only canonical runtime files under `runtime/sites`,
`runtime/infrastructure`, and `runtime/operations`. It writes:

- `runtime/dashboard/wallboard-summary.json`
- `runtime/dashboard/operations/operations-wallboard.json`

The generated dashboard embeds named CSV frames and queries them through the
provisioned built-in TestData datasource using its `csv_content` scenario. See
[Dashboard Data Binding](dashboard-data-binding.md). The mounted JSON files are
not queried directly by Grafana.

The `$site` variable displays registry names and filters internally with stable
`site_id` values. All Sites uses `all`. Site-aware CSV tables carry an explicit
scope column; collector health remains estate-wide because collectors are not
currently assigned authoritatively to individual sites.

## Topology semantics

The topology is a **logical aggregate topology**, not discovered or inferred
physical connectivity. It presents deterministic layers—Internet/WAN, edge,
core, distribution, access, wireless, servers, and printing—using canonical
device classes. Counts and health are real; the seven edges describe the display
hierarchy only. No named-device links are invented.

## Freshness and degraded states

Freshness is based on the oldest generated timestamp across site, infrastructure,
and operations outputs. The configured `wallboard.freshness_seconds` threshold
defaults to 900 seconds. Collector rows separately show last run, last successful
run, duration, consecutive failures, and data age.

Missing services, client metrics, WAN classification, traffic, latency, or packet
loss render as `Awaiting telemetry`, `Unknown`, or `N/A`. Missing data is never
converted to a healthy zero. Infrastructure Health and Observability Health remain
separate.

## Navigation and extension

UID-relative links open Infrastructure Overview, Mist Infrastructure Overview,
and FortiGate Infrastructure Overview without deployment-specific hostnames.
Future work may replace neutral WAN panels with known canonical time series or
the logical topology table with reliable relationship data. It must not infer
interfaces or physical links from traffic volume.
