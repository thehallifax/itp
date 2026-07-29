# Canonical identity

ITP uses one profile-owned identity contract at every layer:

| Field | Purpose | example-school reference value |
|---|---|---|
| `deployment_id` | Deployment and runtime partition | `example-school` |
| `customer_id` | Customer or tenant partition | `example-school` |
| `site_id` | Canonical site join | `site:example-school` |
| `device_id` | Collector-qualified device identity | `paloalto:<serial>` |

IDs are stable, machine-safe, case-sensitive values. Their configured case is
preserved; alias matching is case-insensitive and whitespace-normalised.
Collectors, inventory reconciliation, operations, service health and dashboards
join on these IDs. They must not derive a new identity independently.

`customer_name`, `site_name`, `hostname` and `display_name` are presentation
metadata. Renaming them does not change identity. A local `sites.local.yml`
overlay may change site names and aliases, but it must retain the exact canonical
site ID set from tracked `sites.yml`.

## Ingestion and compatibility

The profile resolver normalises a recognised legacy site alias as configuration
is loaded. Profile-scoped telemetry carries `deployment_id`, `customer_id`,
`site_id` and `collector`; device measurements also carry `device_id` and
`hostname`.

The temporary `customer` and `site` tags contain the corresponding canonical ID,
not a display name. The writer rejects conflicting canonical and compatibility
tags. These compatibility tags remain queryable during the Alpha period and may
be removed after existing deployments have regenerated telemetry and dashboards.

Historical points written with display names or slugs remain queryable directly,
but are not mixed silently into canonical selectors. Doctor/profile validation
should be used before regeneration; a clean database reset is the supported example-school
reference migration.

## Dashboard variables

Managed SQL dashboards return `__text` and `__value`: friendly names are shown to
operators, while canonical IDs are submitted to queries. Customer, site and
device filters use `customer_id`, `site_id` and `device_id`. The `All` value
renders as the SQL-safe wildcard `LIKE '%'` (stored in JSON as `"'%'"`).

New collectors participate by accepting the resolved connector configuration,
emitting the required identity tags, and preserving their deterministic
collector-qualified `device_id`.
