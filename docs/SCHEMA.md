# Canonical schema

Collector support and runtime evidence are separate contracts. See
[Collector capability manifests](collector-capabilities.md). Consumers must not
infer support from missing telemetry.

The authoritative join contract is `deployment_id`, `customer_id`, `site_id`
and, for device-scoped records, `device_id`. Display names are metadata and must
not be used as joins. See [Canonical identity](canonical-identity.md).

Current platform schema version: **1**. Canonical measurements are `device`,
`availability`, `performance`, `interface`, `wireless`, `firewall`, and
`collector_health`. Vendor-specific and earlier normalized measurements remain during
the compatibility period. No migration is required.

Specifications:

- [device](schema/device.md)
- [availability](schema/availability.md)
- [performance](schema/performance.md)
- [interface](schema/interface.md)
- [wireless](schema/wireless.md)
- [firewall](schema/firewall.md)
- [server](schema/server.md)
- [inventory](schema/inventory.md)
- [relationship](schema/relationship.md)
- [collector health](schema/collector_health.md)

The profile-level collector-to-measurement contract and live validation method
are documented in [Measurement contracts](measurement-contracts.md).
