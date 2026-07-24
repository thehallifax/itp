# Operations Wallboard

Operations Wallboard is a single-screen, exception-driven Grafana view for a
16:9 IT office, NOC, school technology office, or MSP display. It complements,
but does not replace, Infrastructure Overview and vendor drill-down dashboards.

## Display

Open dashboard UID `itp-operations-wallboard`, choose All Sites or one canonical
site, and use kiosk mode:

```text
/d/itp-operations-wallboard?orgId=1&kiosk
```

The dashboard uses a bounded 18-row grid with no collapsible sections or
vertical scrolling at standard 1920 × 1080 browser zoom.

## Operational content

The wallboard displays:

- site name and canonical overall service state;
- service-health generation time and stale-data state;
- compact status cards for enabled canonical services;
- healthy/offline/unknown cards for enabled wireless, switching, compute and
  firewall capabilities;
- authoritative WAN uplinks and RX/TX only where classified telemetry exists;
- actionable printer exceptions;
- compact enabled-collector state;
- one priority-ordered **Action Required** table.

Longer-term governance risks and recommendations remain in detailed operational
dashboards. Collection duration, points, retries, and errors remain in Collector
Health.

## Service-health dependency

`runtime/services/service-health.json` is authoritative for overall health and
the Internet, Wireless, Switching, Printing, Identity, Compute, Storage, Voice,
Email, Security, and Monitoring states. The wallboard does not recalculate
these states from assets, findings, collector records, or Grafana queries.

Overall status considers enabled services only within the selected canonical
site. All Sites uses the separately evaluated estate aggregate:

1. Critical if any enabled service is Critical.
2. Warning if none is Critical and at least one is Warning.
3. Unknown if no service is Critical or Warning and an enabled service is
   Unknown, or if no service is enabled.
4. Healthy only when every enabled service has sufficient Healthy evidence.

Not Enabled services never degrade overall status. Their large cards are
omitted and their names appear in a compact neutral header list. Unknown and
Not Enabled are always grey.

## Printer exception policy

Included conditions are printer offline, paper jam, waste-toner full or
critically depleted, toner below 5%, staples empty, and explicitly classified
service-blocking faults.

Paper tray empty, low paper, healthy inventory, general printer counts, and
informational or user-remediable consumables are excluded. With no actionable
condition the panel shows **No printer action required**. When Printing is Not
Enabled, the section is omitted.

## Capabilities, WAN and drill-down

`runtime/dashboard/managed/registry.json` controls domain availability and
drill-down links. The wallboard does not check vendor names to decide whether a
domain exists. Links are added only for dashboard UIDs selected by the registry,
so optional packs may be absent safely.

Unclassified interfaces never become WAN uplinks. Without authoritative
classification or traffic history, compact unavailable text replaces WAN
graphs. Latency and packet loss remain absent until reliable telemetry exists.

## Data binding and filtering

The renderer consumes canonical service health, site, infrastructure,
operations, and capability-registry outputs. Inventory supplies counts only;
operations findings supply the Action Required list only. It writes:

- `runtime/dashboard/wallboard-summary.json`
- `runtime/dashboard/operations/operations-wallboard.json`

The generated dashboard embeds named CSV frames through the provisioned TestData
`csv_content` scenario. Grafana does not query runtime JSON directly. `$site`
uses canonical `site_id` values; All Sites uses the explicit `all` scope.

Every service card contains an explicit row for `all` and every canonical
`site_id`; the renderer never filters an estate row as though it were site
data. Collector State is partitioned by collector coverage, and All Sites
includes site context when a collector covers multiple sites. A site with no
current technician actions displays **No action required** rather than No data.

Freshness uses only the canonical service-health generation timestamp. The
default stale threshold is 900 seconds.

## Regeneration

```sh
docker compose exec collector python -m collectors services generate
docker compose exec collector python -m collectors wallboard generate
docker compose exec collector python -m collectors dashboards generate
docker compose exec collector python -m collectors dashboards status
docker compose exec collector python -m collectors validate
docker compose restart grafana
docker compose logs --since=5m grafana
docker compose logs --since=5m collector
```
