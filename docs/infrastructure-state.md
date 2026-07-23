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

## Outputs and schema

The scheduled engine atomically creates `runtime/infrastructure/state.json`,
`state.csv`, and the intentionally flat
`runtime/dashboard/infrastructure-summary.json`. State contains `generated_at`,
sites, summary, network, wireless, firewalls, servers, printers, collectors,
validation warnings, and the deduplicated canonical assets.

Counts are derived only from explicit inventory values. Unknown online state is
kept unknown. Wireless clients, failed authentication, printer consumables, and
WAN state are `null` when the existing outputs do not provide them.

## Merge rules

Adapters are evaluated using fixed precedence:

1. Inventory (`300`)
2. Vendor collectors—Mist and FortiGate (`200`)
3. SNMP discovery (`100`)

Identity uses serial number, then hostname, then management IP, then asset ID.
The highest-priority record owns populated values; lower-priority records may
only fill missing fields. Conflicting explicit online states create a warning and
never override the higher-priority value. Assets and warnings are sorted by stable
keys, so identical inputs produce byte-equivalent logical output apart from the
requested generation timestamp.

Validation warnings cover duplicate serials, duplicate hostnames, conflicting
states, missing sites, and missing management IPs. They are evidence, not alerts.

## Adapter API

Subclass `SignalAdapter`, set a unique `name` and integer `priority`, and implement
`collect()` returning `AdapterResult`. Subclasses register automatically. Missing
collector files must return an empty result; absence is not an exception.

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
