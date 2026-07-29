# Dashboard navigation

Managed dashboards filter with `customer_id`, `site_id`, and stable device
identity. Variables may display `customer_name` or `site_name`, but their values
remain canonical IDs. Vendor `source_site_name` is diagnostic, not an
aggregation key. Readiness comes from deployment state and framework-owned
`collector_health`, not from a missing telemetry query.

Managed dashboards use the
[collector capability manifest](collector-capabilities.md) for implemented,
conditional, disabled, unavailable, failed and unsupported states. Dashboard
authors must not infer support from a null query result.

Managed dashboard variables show friendly labels but filter on canonical
`customer_id`, `site_id` and `device_id` values. Display names must never be
used as SQL joins. `All` uses a quoted SQL wildcard with `LIKE`.

Dashboard selection and folder provisioning are capability-aware. See
[Dashboard Platform](dashboard-platform.md) for manifest and ownership details.

ITP dashboards are operations-first. Begin with **Infrastructure Overview** to
identify service impact, unhealthy infrastructure, active issues, and emerging
risks. Move into the relevant operational domain for investigation, then use a
vendor dashboard only when collector-specific detail is required.

Use **Operations Wallboard** for a continuous 16:9 kiosk display. It provides a
restrained, exception-driven estate or selected-site view without scrolling and
uses canonical service health for overall and domain status. Inventory is used
only for vendor-neutral counts, not service-state calculation. Links lead only
to applicable provisioned detail dashboards. See
[Operations Wallboard](operations-wallboard.md).

Its top row is service-first and glanceable: cards show concise canonical
states, while descriptions retain evidence and drill-down destinations.
Monitoring derives from canonical collector health, Internet reports classified
WAN evidence, and Firewall can surface the highest-priority Security cue.
Collector execution fields remain on the Collector Health drill-down.

Canonical summary panels use the supported generated-dashboard projection
described in [Dashboard Data Binding](dashboard-data-binding.md). Runtime JSON is
never treated as a Grafana-readable datasource.

## Information architecture

The first column below is a normalized provisioned folder. Packs appear only
when enabled collector capabilities support them.

```text
Operations/{Operations Wallboard, Collector Health}
Infrastructure/{Infrastructure Overview}
Security/{future capability packs}
Wireless/{future capability packs}
Printing/{future capability packs}
Compute/{future capability packs}
Identity/{future capability packs}
Vendor/{enabled vendor drill-downs}
```

The registry materializes selected dashboards into a dedicated managed runtime
namespace. Mist, FortiGate, and Palo Alto dashboards retain stable UIDs and
queries but appear only when their collectors are enabled.

## Dashboard design order

Operational dashboards should progress through **Summary**, **Health**,
**Performance**, **Problems**, and **Historical Trends**. Sections may be omitted
when their telemetry does not yet exist, but the order should remain consistent.

The Infrastructure Overview reads device counts, Infrastructure Health,
Observability Health, and actionable warnings from the flat canonical-state
summary. It also displays total, healthy, warning, and critical canonical sites.
Its `$site` variable uses stable registry IDs and is populated by the generated
dashboard. Active Issues, Operational Risks, and Recommendations are rendered
as canonical-site-partitioned frames from the deterministic
[Operations Engine](operations-engine.md) output. Findings join only through
`site_id`; display names are labels, never join keys. All Sites uses an explicit
estate scope. Clearly
labelled TODO panels remain only where telemetry does not exist. Future dashboards
should consume vendor-neutral telemetry and analysis outputs; vendor-specific
measurements belong only in Vendor drill-downs.

## Expansion

Add dashboards to the appropriate leaf directory. Avoid creating a new top-level
domain unless the dashboard cannot reasonably fit the existing navigation model.
Collectors must not contain dashboard logic, and operational dashboards should
not require knowledge of a vendor collector.

Palo Alto firewalls therefore enter generic Firewall and Security/Edge panels
through canonical classification. COL-PA-001 adds no vendor dashboard and does
not alter FortiGate panels.

The generated **Palo Alto Operational Overview** source is written to
`runtime/dashboard/grafana/paloalto-overview.json`; the registry copies its
managed dashboard pack into the normalized Vendor folder:

```sh
python scripts/generate_paloalto_dashboard.py
docker compose exec collector python -m collectors dashboards generate
docker compose restart grafana
```

It queries confirmed canonical `device`, `firewall`, `performance`, `interface`,
`license`, `content_package`, and `collector_health` fields. Implemented panels
use **Not Yet Collected** when a current row is absent. Configuration commits
and certificate expiry explicitly show **Feature Not Enabled** because the
current measurement contract does not expose those fields.

## Upgrade note

Managed dashboards are replaced by stable UID and disabled packs are removed.
User-created dashboards remain in Grafana storage and are never written into the
managed runtime directory. Legacy empty folders from older provisioning layouts
can remain in an upgraded Grafana database; they contain no managed dashboards
and may be removed after confirming they contain no user dashboards.
The Palo Alto operational dashboard uses canonical resource, session,
interface-counter, subscription, content-package, certificate, and collector
diagnostic telemetry. Classified WAN interfaces are discovered by a hidden
FlightSQL variable and rendered as independently scaled repeated panels; the
friendly WAN label is display text and the interface name remains the query
value. Collector diagnostic Stat panels display values without internal field
aliases, and Last Successful Collection uses the latest successful
collector-health row.
Run `python -m collectors dashboards generate` after
collector or dashboard upgrades; managed files remain replaceable and preserve
the stable `paloalto-operational-overview` UID.

The conditional **Virtualisation / Virtualisation Overview** dashboard is
provisioned only when virtualisation is enabled for the active profile. It uses
canonical `virtualisation_*` FlightSQL measurements and never calls vendor APIs.

The Operations Wallboard packs panels deterministically after capability
filtering, so disabled services leave no gaps. Release evidence generated by
`scripts/render_wallboard_scenario.py` remains under ignored
`runtime/evidence/` and follows the same classic managed-dashboard contract.
