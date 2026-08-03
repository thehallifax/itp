# Operations Wallboard

Service descriptions inherit collected, unavailable, failed and deliberately
unsupported evidence from canonical Service Health. Visible stats stay concise.

Wallboard scope values are canonical site IDs; labels come from resolved site
metadata. Certificate attention text and printer empty states are deliberately
compact for wallboard-distance reading.

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

- eight operator-facing cards: Issues, Overall Health, Monitoring, Security,
  Internet, Firewall, Printing, and Certificates;
- optional cards for enabled future and virtualisation services;
- one readable Download/Upload graph per authoritative WAN interface;
- operational changes observed during the last 24 hours;
- one fleet-specific **Printers Requiring Attention** table;
- one priority-ordered, action-oriented **Action Required** table.

Longer-term governance risks and recommendations remain in detailed operational
dashboards. Collection duration, points, retries, and errors remain in Collector
Health. The landing page does not expose a collector-state table.

The Monitoring card displays the canonical Monitoring status. Its description
records how many enabled collectors require attention, the latest successful
collection, and services with stale coverage. It links to Collector Health;
connector internals remain there and in `./itp doctor`.

Overall Health and Security display concise canonical states. Their descriptions
retain the Service Health explanation and evidence. Firewall may promote the
highest-priority certificate/subscription cue while retaining the canonical
Security severity and firewall drill-down.
Certificates is a focused projection of the same Operations evidence, including
certificate and subscription expiry findings; it cannot report Healthy while a
matching action remains active.

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

Before canonical service evidence exists, the wallboard consumes
`runtime/dashboard/readiness.json`. A clean deployment displays **Monitoring
not started**; an enabled collector without a successful run displays
**Awaiting first collection**; failed or stale collection displays
**Collectors unavailable**. These onboarding states never replace a real
Warning or Critical service result.

## Printer exception policy

Included conditions are printer offline, paper jam, waste-toner full or
critically depleted, toner below 5%, staples empty, and explicitly classified
service-blocking faults.

Paper tray empty, low paper, healthy inventory, general printer counts, and
informational or user-remediable consumables are excluded. With no actionable
condition the panel shows **No printer action required**. When Printing is Not
Enabled, the section is omitted.

## Capabilities, WAN and drill-down

`runtime/deployments/<deployment>/generated/dashboard/managed/registry.json`
controls domain availability and
drill-down links. The wallboard does not check vendor names to decide whether a
domain exists. Links are added only for dashboard UIDs selected by the registry,
so optional packs may be absent safely.

Unclassified interfaces never become WAN uplinks. The Internet card is derived
directly from authoritative WAN signals and uses evidence-based wording such as
**2 / 2 WANs Healthy**, **1 / 2 WANs Healthy**, and **Not Yet Collected**. It
does not create a second Internet calculation.

Each classified interface receives its own responsive graph named with both its
friendly role and display name. The SQL predicate always uses the canonical
PAN-OS `interface_name`; `display_name` is presentation metadata only. Download
and Upload use bits per second,
Grafana auto-scaling, and a table legend with the latest value. One WAN uses the
full row; two WANs use equal columns; an odd final WAN uses the full following
row. These panels query collector-derived `rx_bps` and `tx_bps` directly from
the canonical InfluxDB `interface` measurement, bounded by site, device,
interface and dashboard time range. The state summary retains a hidden CSV
fallback for portable inspection. The first counter observation establishes a
baseline; subsequent observations create rates. Missing observations remain
gaps, are never converted to zero, and are not connected across outages longer
than three minutes. An interface without samples displays an explicit waiting
state instead of generic No data.

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
data. Monitoring collector coverage is deduplicated per canonical scope. A site
with no current technician actions displays **No action required** rather than
No data.

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

For a deployment profile, use the profile-aware wrappers:

```bash
./itp profile services <profile>
./itp profile wallboard <profile>
./itp profile dashboards <profile>
./itp profile restart <profile>
```

Virtualisation uses the existing wallboard rather than a vendor wallboard.
`Action Required` includes provider-neutral domain, provider and object-kind
columns, covering manager/collection health, clusters, hosts, workloads,
capacity, storage and snapshot governance. Site filtering remains canonical and
SQL-free because generated frames use exact `site_id` scopes.

## Final layout

The renderer packs the grid after capability filtering:

1. Eight equal operator-facing status cards.
2. Responsive, eight-row-high per-interface WAN traffic graphs.
3. Equal-width Action Required and Changes Since Yesterday tables.
4. A full-width Printers Requiring Attention table.
5. Capability-gated service and compact infrastructure detail.

Action Required sorts by severity, evidence age and priority. Compact columns
show Severity, Service, Asset, Action, and Age. Findings are rendered as
operator actions—for example, **Renew DNS Security certificate today** and
**7 printers require attention**. Provider and object diagnostics remain in
linked detail dashboards. Relative freshness uses `Just now`, minutes, hours
and days.

Changes Since Yesterday consumes persisted state-history change sets and
canonical Service Health `last_change` evidence. It reports recoveries and
material operational transitions from the previous 24 hours without creating a
second event engine.

Site Operational Status is the selected scope's active issue count. A service
in Warning or Critical state must have a matching actionable row; Medium
virtualisation risks are therefore included. WAN and Servers appear only when
their canonical capabilities are enabled. Virtualisation-only evidence omits
empty generic Compute/Storage cards and uses concise service titles.

## Release screenshot fixtures

Sanitized evidence is generated below `runtime/evidence/` and never enters a
profile's canonical runtime:

```bash
.venv/bin/python scripts/render_wallboard_scenario.py example-corporate
.venv/bin/python scripts/render_wallboard_scenario.py vmware
.venv/bin/python scripts/render_wallboard_scenario.py hyperv
.venv/bin/python scripts/render_wallboard_scenario.py proxmox
```

Each scenario contains a complete managed dashboard and provisioning file under
`runtime/evidence/<scenario>/dashboard/`. example-corporate demonstrates wireless and Mist
state; the remaining scenarios reuse sanitized provider fixtures. Use
1920×1080 for primary release images and 1440×900 for the compact-layout check.
