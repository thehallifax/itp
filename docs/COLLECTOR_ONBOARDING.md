# Collector onboarding

The connector registry is the authoritative catalogue for configuration,
credential fields, validation support, dashboards, implementation, and
operator guidance.

```bash
./itp collector list
./itp collector add <collector>
./itp collector test <collector>
./itp collector remove <collector>
./itp dashboard generate
```

Add writes only to the selected runtime deployment. Secret fields use hidden
input and may be left blank for later completion. Remove disables collection;
it does not delete historical telemetry or user-created dashboards.
Only capabilities from explicitly enabled collectors appear on live
operational dashboards. Discovery can report a possible product without
selecting or enabling it.

Prompt labels, examples, defaults, types, sensitivity, normalization, and
canonical config/credential equivalence are declared in the connector registry.
This keeps onboarding deterministic and prevents the same endpoint being
requested twice.

- FortiGate accepts `hostname[:port]` or an HTTPS origin. Do not enter
  `/api/v2`; the host is stored as an HTTPS origin and the token is hidden.
- Mist requires a complete HTTPS origin. The default is
  `https://api.mist.com`; Australian organisations commonly use
  `https://api.ac2.mist.com`. Do not include `/api/v1`.
- PaperCut accepts `hostname[:port]` or an HTTPS origin. A trailing
  `/api/health` is safely removed, and the optional authorization key is hidden.

Connection tests execute the registered read-only collector inspection and
return redacted output. Consult the collector-specific document when an
endpoint, role, certificate, or regional API setting is required.

## Collector lifecycle contract

Every enabled connector follows:

```text
Discover → Collect → Normalise → Validate → Write → Health → Summary
```

Collectors provide source metadata and mapped values. Deployment identity,
schema enforcement, writes, run health, and readiness belong to the framework.
An edge-only connector in a central runtime remains visible as `skipped` with
an explicit placement reason.

For each selected connector, complete configuration and credentials, run
`collector test`, then run one collection:

```bash
./itp collector test <collector> --deployment <deployment>
./itp collector run <collector> --deployment <deployment>
./itp doctor --deployment <deployment>
./itp dashboard generate --deployment <deployment>
```

The generated deployment lives at
`runtime/deployments/<deployment>/`. Safely rerun `collector add` to amend
configuration; existing values are preserved unless the operator replaces
them.

Palo Alto WAN health requires an explicit `wan_interfaces` list in the
deployment's ignored `collectors.yml`. Inspect candidate names in the Palo Alto
Interface Inventory dashboard or collector output, then configure:

```yaml
collectors:
  paloalto:
    enabled: true
    wan_interfaces:
      - name: ethernet1/1
        role: primary
        display_name: Primary Internet
```

Until it is configured, the operational state is **WAN role not configured**,
not a collector failure.
