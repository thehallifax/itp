# Collector onboarding

The connector registry is the authoritative catalogue for configuration,
credential fields, validation support, dashboards, implementation, and
operator guidance.

```bash
./itp collector list
./itp collector add <collector>
./itp collector setup <collector>
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
  `https://api.ac2.mist.com`. Do not include `/api/v1`. Its organisation ID is
  a non-secret UUID stored in canonical deployment configuration; the API token
  remains in the deployment secret file. Authentication, permissions, regional
  endpoint mismatch, malformed response, and transport failures are reported
  as distinct safe diagnostic categories.
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

`deploy --deployment-id <id> --force` resumes an existing deployment and
preserves its canonical configuration and credential files. Use
`--reconfigure` only when the operator explicitly intends to reopen onboarding.
Read-only commands never migrate or rewrite deployment configuration.

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

## Guided setup

Palo Alto, FortiGate and PaperCut support guided setup:

```bash
./itp collector setup paloalto --deployment <deployment>
./itp collector setup fortigate --deployment <deployment>
./itp collector setup papercut --deployment <deployment>
```

The workflow preserves credential references, performs the existing read-only
connection inspection, reports partial capabilities, and offers a first
collection. TLS inspection distinguishes DNS, TCP, trust, hostname, expiry and
timeout failures. Import a private CA rather than disabling verification:

```bash
./itp credentials ca add <certificate> --deployment <deployment>
```

Palo Alto and FortiGate setup lists discovered interfaces and recommends likely
WANs using device metadata. Recommendations require explicit selection. The
saved `name` remains canonical device identity; alias/description is suggested
only as `display_name`. Manual mappings that are temporarily absent are
preserved with a warning. Exactly one selected WAN must be primary.
