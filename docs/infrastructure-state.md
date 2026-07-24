# Infrastructure State

Infrastructure State is the vendor-neutral boundary between collector outputs and
operational consumers. It deterministically adapts the existing inventory and
collector run files into one canonical state used by the Operations Engine,
Grafana, and future local APIs.

```text
Collectors → Signal Adapters → Infrastructure State
                                  ├─ Operations Engine
                                  ├─ Grafana
                                  └─ Future API
```

Before fusion, the [Canonical Site Registry](site-registry.md) resolves exact
configured aliases to stable `site_id` values. Assets retain the original value
from every source in their site provenance.

## Outputs and schema

The scheduled engine atomically creates `runtime/infrastructure/state.json`,
`state.csv`, the canonical estate files under `runtime/sites/`, and the intentionally flat
`runtime/dashboard/infrastructure-summary.json`. State contains `generated_at`,
sites, summary, network, wireless, firewalls, servers, printers, collectors,
validation warnings, and the deduplicated canonical assets.

The presentation-only Wallboard Engine derives scoped domain totals and logical
aggregate topology from this state. It does not create a second asset registry or
feed presentation values back into Infrastructure State.

Counts are derived only from explicit inventory values. Unknown online state is
kept unknown. Wireless clients, failed authentication, printer consumables, and
WAN state are `null` when the existing outputs do not provide them.

## Canonical fusion

Adapters are evaluated using fixed precedence:

1. Inventory (`300`)
2. Vendor collectors—Mist, FortiGate and Palo Alto Networks (`200`)
3. SNMP discovery (`100`)

Identity uses serial, chassis ID, management MAC, hostname/site, IP/site, then a
source-native ID. The highest-priority source owns populated values and lower
priority sources fill gaps. Status additionally considers observation freshness
and vendor authority. Conflicts remain explicit. See
[Canonical Asset Model](canonical-asset-model.md) for the confidence model,
provenance, collision safeguards, and worked example.

Validation covers remaining duplicate identities, conflicting states, missing
sites, and device-aware management-IP gaps. Findings are classified as
actionable, data quality, or suppressed. State separately reports Infrastructure
Health and Observability Health.

## Adapter API

Subclass `SignalAdapter`, set a unique `name` and integer `priority`, and implement
`collect()` returning `AdapterResult`. Subclasses register automatically. Missing
collector files must return an empty result; absence is not an exception.
PAN-OS records use serial-first fusion, including fusion with SNMP. HA peer
metadata never creates a second asset without a direct observation.

Existing adapters are Inventory, Mist, FortiGate, and SNMP. They consume only the
current runtime inventory and source-run outputs and do not call vendor APIs.

```sh
docker compose exec collector python -m collectors infrastructure adapters
docker compose exec collector python -m collectors infrastructure generate
```

## Extension guide

Future adapters may consume existing normalized telemetry snapshots for wireless
clients, service state, storage, or WAN quality. They must remain offline,
deterministic, vendor-neutral after adaptation, and must represent unavailable
values as `null` rather than inferred data.
