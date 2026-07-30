# Edge collector architecture

ITP uses one collector runtime in two placement modes. `central` is the control-plane
default and runs internet-accessible API collectors such as Mist. `edge` runs near
private infrastructure and is the normal placement for FortiGate API and SNMP.
Collectors declared `either` can run in both. Configuration, not separate codebases,
selects placement.

## Responsibilities

The central control plane owns tenant/site policy, collector enrollment, configuration,
normalized ingestion, storage, inventory, analysis, dashboards, reporting, and upgrade
coordination. An edge collector owns local authentication, discovery, collection,
normalization, batching, and a durable retry queue. Vendor credentials remain local.

Future edge communication is outbound-only HTTPS. Enrollment will issue a collector
identity bound to tenant and site. Requests will use short-lived collector credentials
or signed payloads, carry idempotency keys, and receive an acknowledgement only after
the central service durably accepts a batch. The edge retains unacknowledged batches,
backs off with bounded retries, operates while disconnected, and applies centrally
approved signed upgrades. None of that public ingestion service or queue is implemented
in Phase 1.

## Draft ingest envelope

```json
{
  "schema_version": 1,
  "collector_id": "edge-wa-001",
  "tenant_id": "customer-id",
  "site_id": "site-id",
  "batch_id": "stable-idempotency-key",
  "generated_at": "2026-07-22T12:00:00Z",
  "records": []
}
```

`batch_id` is stable across retries. Records use the normalized telemetry contract and
must not include vendor credentials. A future acknowledgement identifies the accepted
batch and receipt time; ambiguous failures remain retryable.

## Phase 1 placement

```yaml
collectors:
  mist: {enabled: true, execution: central}
  paloalto: {enabled: true, execution: either}
  fortigate: {enabled: true, execution: edge}
  snmp: {enabled: true, execution: edge}
```

`ITP_RUNTIME_MODE` accepts `central` or `edge` and defaults to `central` to preserve the
existing Mist deployment. Missing execution metadata uses the collector's registered
default. Unsupported values fail early.

Palo Alto uses only the remote PAN-OS management XML API, so it can execute in
either runtime when that runtime can reach the management endpoint. A genuine
placement mismatch is recorded as `execution_mode_mismatch` with both modes
and remediation; it is a connector skip rather than a generic failure.

SNMP remains an edge fallback and interface-counter enrichment path. API identity is
preferred; serial is the strongest FortiGate identity, with a deterministic hostname or
host fallback. Inventory reconciliation can associate API and SNMP observations through
serial, management IP, and hostname evidence.
