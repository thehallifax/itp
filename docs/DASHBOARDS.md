# Dashboard navigation

ITP dashboards are operations-first. Begin with **Infrastructure Overview** to
identify service impact, unhealthy infrastructure, active issues, and emerging
risks. Move into the relevant operational domain for investigation, then use a
vendor dashboard only when collector-specific detail is required.

## Information architecture

The first column below is the provisioned Grafana folder. Indented entries are
the dashboards planned for that folder; only Infrastructure Overview and the
two preserved Vendor drill-downs are delivered in OPS-001.

```text
Infrastructure Overview
Network/{Network Health, Switching, Wireless, Firewalls, WAN}
Compute/{Servers, Storage, Virtualisation}
Printing/{Fleet, Consumables, Faults}
Services/{Active Directory, DNS, DHCP, Certificates}
Inventory/{Assets, Discovery, Lifecycle, Changes}
Collectors/{Health, Performance}
Operations/{Active Alerts, Risks, Recommendations}
Vendor/{Mist, FortiGate}
```

Grafana file provisioning mirrors this repository hierarchy automatically. The
Mist and FortiGate dashboards remain engineering drill-down dashboards and keep
their existing UIDs and queries.

## Dashboard design order

Operational dashboards should progress through **Summary**, **Health**,
**Performance**, **Problems**, and **Historical Trends**. Sections may be omitted
when their telemetry does not yet exist, but the order should remain consistent.

The Infrastructure Overview reads device counts, Infrastructure Health,
Observability Health, and actionable warnings from the flat canonical-state
summary. Active Issues, Operational Risks, and Recommendations are rendered from
the deterministic [Operations Engine](operations-engine.md) output. Clearly
labelled TODO panels remain only where telemetry does not exist. Future dashboards
should consume vendor-neutral telemetry and analysis outputs; vendor-specific
measurements belong only in Vendor drill-downs.

## Expansion

Add dashboards to the appropriate leaf directory. Avoid creating a new top-level
domain unless the dashboard cannot reasonably fit the existing navigation model.
Collectors must not contain dashboard logic, and operational dashboards should
not require knowledge of a vendor collector.

## Upgrade note

The fixed `itp-folder-overview`, `itp-folder-network`, `itp-folder-compute`, and
`itp-folder-vendor` UIDs are retained for existing installations. Grafana does
not rename a previously provisioned folder when its display name changes, so an
upgraded instance may continue to label Infrastructure Overview as `Overview`.
The dashboard itself and both Vendor dashboards are relocated correctly. A new
deployment receives the complete folder names from this provisioning file.
