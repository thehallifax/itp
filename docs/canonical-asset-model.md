# Canonical Asset Model

ITP fuses collector observations into one physical-asset record before state,
operations, or dashboard evaluation. Fusion is deterministic and offline: the
same observations always produce the same `canonical_id`, fields, provenance,
conflicts, and ordering.

Configured site aliases are resolved to stable canonical site IDs before this
identity evaluation. Original source labels remain under `site.source_values`.

## Identity and confidence

Identity evidence is evaluated from strongest to weakest: serial number,
chassis ID, management MAC, hostname plus site, management IP plus site, then a
source-native ID. Hostnames are case-insensitive, trailing dots are removed, and
FQDN/short-name matches are supported. Serial or incompatible device-type
conflicts prevent a merge, including transitive bridge merges.

Confidence is `exact` for shared hardware identity, `high` for compatible
hostname/site identity, `medium` for compatible IP/site evidence, `low` for an
ambiguous candidate, and `unmerged` when no match exists. Low-confidence records
remain separate and produce a review finding; ITP never guesses.

## Fusion policy

Field authority is fixed: inventory (`300`), vendor adapters such as Mist and
FortiGate (`200`), then SNMP (`100`). A lower-authority source fills missing
values but cannot replace a populated higher-authority value. Every selected
field records its source under `field_provenance`.

Status is handled separately. Fresh explicit status is preferred over stale
status; within the freshness window a vendor collector outranks discovery. A
fresh online/offline disagreement is retained as a merge conflict. The default
window is 300 seconds and is configurable with
`infrastructure.status_freshness_seconds`.

Each canonical asset exposes `canonical_id`, `sources`, `source_records`,
`field_provenance`, normalized MAC addresses, and a `merge` object containing
confidence, matched evidence, and structured conflicts.

### Worked Mist and SNMP example

Mist reports `coreedge.example.edu` at the full customer site name. SNMP reports
`COREDGE` at the site's configured discovery alias. The registry resolves both
labels to one `site_id`, then compatible hostname and management-IP evidence
allows a high-confidence merge. Mist owns current status
and vendor fields; SNMP may fill a missing management field. Both source records
remain visible and the resulting ID is stable.

## Validation and health

Validation is device-aware. Missing management IP on an offline access point is
an informational, suppressed finding; the same gap on a managed core switch is
actionable. Findings are separated into actionable warnings, data-quality
findings, and suppressed findings.

Infrastructure Health describes assets and services. Observability Health
describes collector coverage. A failed collector can therefore reduce
observability without falsely declaring working infrastructure unhealthy.

Inspect a deployment without revealing credentials:

```sh
docker compose exec collector python -m collectors infrastructure fusion-report
```

## Extending adapters

An adapter supplies observations with stable `source_asset_id`, source name,
observation time, identity fields, and explicit state when available. It must not
perform fusion itself. Add source authority or field-specific policy only when
the source contract warrants it, then cover collision, ordering, and absence in
tests.
