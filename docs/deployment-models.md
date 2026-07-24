# Deployment models

**Different customer or security boundary: create a new profile.**

**Same organisation requiring combined reporting: add another site to the
existing profile.**

```text
Is combined reporting and credential trust appropriate?
├── No  → new deployment profile
└── Yes → site in the existing profile
          ├── peer locations → flat multi-site
          └── parent/child relationship → hierarchical multi-site
```

## Supported models

- `single_site`: one enabled site; dashboards select it directly.
- `multi_site_flat`: several peer sites and an Entire Estate selection.
- `multi_site_hierarchical`: root sites and children plus an Entire Estate selection.
- Multiple profiles: isolated Docker Compose projects, Grafana instances,
  InfluxDB storage, secrets and runtime directories.

Tracked configurations are under `examples/deployments/`. Metadata is optional
for existing single-site profiles:

```yaml
sites:
  - id: head-office
    name: Example Head Office
    type: head_office
  - id: campus-one
    name: Campus One
    type: school
    parent_id: head-office
```

An estate dashboard aggregates only enabled sites in its profile.
`deployment_id` remains the customer boundary and `site_id` the location
boundary. Secrets live under `secrets/<profile>/`; generated files live under
`runtime/<profile>/`.

To add a site, edit `sites.yml`, add collector aliases, validate and regenerate.
To split an organisation, create a profile and deliberately migrate telemetry;
do not copy runtime or secrets between security boundaries.

Common mistakes include reusing IDs, assigning a parent from another profile,
using the site selector as a customer selector, or inventing a shared service
dependency. See [site hierarchy](architecture/site-hierarchy.md) and the
[operator guide](operator-guide.md).
