# Architecture

ITP has one Compose deployment: InfluxDB stores telemetry, Grafana reads it through
FlightSQL, Telegraf performs SNMP polling, discovery manages SNMP targets, and the shared
collector runtime schedules native API collectors. Runtime mode selects central or edge
execution without separate applications.

Collectors own authentication, discovery, collection, and adaptation. Canonical
measurements, inventory, storage, analysis, and dashboards remain vendor-neutral.
Vendor-specific measurements are transitional compatibility outputs. See
`edge-collector-architecture.md` for the future outbound edge transport contract.
