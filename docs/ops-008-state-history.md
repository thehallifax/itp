# OPS-008 — State History and Safe Pipeline Capture

Phase 1 adds deterministic history for canonical platform state. It
persists observations, compares each site/domain scope with its previous
snapshot, and emits exact changes without reading raw vendor payloads.

## Domain model

- **Observation** — one canonical input document and its observation metadata.
- **EntityState** — one canonical entity identified within a site and domain.
- **StateSnapshot** — immutable normalized entity state for one site/domain
  scope.
- **StateChange** — one entity addition, removal, field change, or operational
  status change.
- **ChangeSet** — all ordered changes between two snapshots of the same scope.

All records use schema version 1. Inputs currently adapt the existing canonical
Infrastructure State (`assets`), Operations (`issues`, `risks`,
`recommendations`), Virtualisation (`objects`), or the explicit generic
`entities` contract.

## Stable identity and scope

An entity identity is the tuple:

```text
site_id / domain / entity_type / entity_id
```

Infrastructure uses canonical asset IDs, Operations uses stable operational
item IDs, and Virtualisation uses canonical virtualisation IDs. Display names
and provider-native identifiers are state or provenance, not join keys.

Snapshots are scoped by canonical `site_id` and `domain`. This makes prior-state
lookup explicit and prevents changes from one customer site or domain leaking
into another. Moving an entity between scopes is represented as removal from
the prior scope and addition to the current scope when both scopes are present
in the canonical observation.

## Comparison semantics

Dictionary keys and emitted changes are sorted deterministically. Nested fields
use dot-separated paths such as `location.rack`. Entity presence and field
values produce:

- `entity_added`
- `entity_removed`
- `field_changed`
- `status_changed`

Fields named `status`, `health`, `state`, `online`, `power_state`,
`connection_state`, `operational_status`, `ha_status`, `reachable`, or
`lifecycle_state` use `status_changed`. Severity is populated only for states
with an unambiguous deterministic classification.

Lists remain ordered unless their canonical field is explicitly declared
unordered. Default unordered fields include tags, sources, addresses, storage
and network IDs, affected assets/services, and evidence. Their elements are
canonicalized and sorted before comparison.

## Volatile fields

Volatile data is excluded explicitly before snapshot comparison. Phase 1
excludes:

```text
generated_at
observed_at
collected_at
timestamp
last_seen_at
source_last_seen_at
last_observed
collection_duration
collection_duration_seconds
uptime_seconds
```

Observation and collection timestamps remain snapshot metadata and are not
silently discarded. Additional store consumers may construct the engine with a
different explicit field policy; vendor-specific exclusions do not belong in
the comparison engine.

## Deterministic identifiers

Snapshot, change, and change-set IDs are SHA-256-derived identifiers over
canonical JSON with sorted keys and compact separators. Equivalent inputs and
comparisons therefore produce the same IDs and ordering. IDs contain no
credentials or raw vendor payloads.

## Filesystem store

`StateStore` defines the replaceable persistence boundary. `FileStateStore`
writes:

```text
<store>/
  snapshots/<snapshot_id>.json
  changes/<change_set_id>.json
  latest/<scope_id>.json
  runs/<pipeline_run_id_hash>.json
```

Every file uses the platform atomic-write helper. Phase 2 writes all immutable
snapshots and change sets before advancing any latest pointer. If a later
pointer or run-result write fails, all affected pointers are restored. Orphaned
immutable files are harmless and reusable because identifiers are stable.
Repeating a completed `run_id` returns its persisted result without advancing
history again. An empty directory is valid,
and the first observation emits `entity_added` records. Runtime stores belong
under ignored `runtime/`; tests use temporary directories. A future SQLite or
PostgreSQL store can implement the same interface without changing comparison.

## CLI

Process an existing canonical document without Docker or deployment
configuration:

```sh
python -m collectors state-history process \
  --input runtime/infrastructure/state.json \
  --store runtime/state-history \
  --json
```

Use `--observed-at 2026-07-24T01:00:00Z` when a fixture has no canonical
timestamp. Invalid JSON, missing timestamps, missing stable identities, and
unsupported document shapes return a non-zero exit status.

Capture a canonical output with explicit pipeline metadata:

```sh
python -m collectors state-history capture-run \
  --input runtime/infrastructure/state.json \
  --run-metadata runtime/pipeline-run.json \
  --store runtime/state-history --json

python -m collectors state-history inspect-run \
  --run-id pipeline-20260724-01 --store runtime/state-history --json
```

## Phase 2 capture contract

`PipelineRun` records run identity, timestamps, status, canonical output, and
site/domain coverage. Every `ObservationScope` records expected, observed,
failed, and skipped sources/providers plus one explicit completeness value:
`complete`, `partial`, `failed`, `skipped`, or `unknown`. `CaptureResult`
records the immutable outcome and suppression counts.

Only a `complete` site/domain scope is authoritative for removals. Partial,
failed, skipped, and unknown scopes may still emit additions and field/status
changes from observed entities, but prior unseen entities are carried forward.
A non-authoritative scope with no observed entities does not advance its latest
pointer. This conservative authority boundary prevents a failed provider or
site from deleting state supplied by another provider or site.

Pipeline capture is disabled by default:

```yaml
state_history:
  enabled: false
  store_path: /app/runtime/state-history
  removal_policy: complete_only
  volatile_fields_policy: default
  expected_sources: []
```

Enabling capture without `expected_sources` is safe: scopes are `partial` or
`unknown` and removals remain suppressed. Configure the full expected-source
contract before granting completeness. `removal_policy: disabled` suppresses
removals regardless of completeness.

Capture runs after the infrastructure canonical document and all other
operational derivatives have been written successfully. A canonical pipeline
failure prevents capture. A history persistence failure is logged clearly as
`state_history result=degraded`, while the already-valid canonical pipeline
remains successful; retrying the same deterministic run is safe.

## Boundary to future events

An observation is source evidence. A state change is a factual difference
between canonical observations. Neither is an alert event. Alert policy,
delivery, acknowledgement, deduplication windows, user rules, scheduling,
retention pruning, trends, and forecasting remain outside Phase 1.

Scheduled collection, retention/pruning, alerting, operational-rule inputs,
database storage, dashboards, and trend analysis remain future work.
