# ITP roadmap

The roadmap extends the vendor-neutral layers after collection. It does not
commit the project to a database or analytics implementation prematurely.

1. **Inventory Engine Foundation** — implemented: stable source identity,
   reconciliation evidence, lifecycle evaluation, and deterministic JSON.
2. **Scheduled Lifecycle Tracking** — implemented: source-aware aging, scheduled
   evaluation, bounded transition history, and explicit retirement/restoration.
3. **Inventory Change Detection Foundation** — implemented: protected identity,
   authoritative comparisons, deterministic classification, suppression, and
   bounded change history.
4. **OPS-008 Phase 1 State History** — implemented: canonical site/domain
   snapshots, deterministic field-level change detection, stable change IDs,
   and replaceable atomic filesystem storage.
5. **OPS-008 Phase 2 Safe Pipeline Capture** — implemented: explicit run and
   completeness metadata, conservative removal authority, atomic multi-scope
   persistence, idempotent capture, and CLI inspection.
6. **OPS-008 Phase 3 History Operations** — define bounded retention and expose
   history queries without treating changes as alerts.
7. **Health Scoring** — calculate explainable, vendor-neutral infrastructure
   health from normalized telemetry.
8. **Relationship Engine** — model site, device, interface, and service
   relationships without collector coupling.
9. **Additional collectors** — add supported vendors through the existing
   registry, configuration, inventory, and writer contracts.
10. **OOBE-002 Connector Onboarding** — Phase 1 implemented: authoritative
    connector metadata and inspection. Phase 2 should add guided onboarding
    only for connectors whose registry capabilities and validation are ready.
11. **CLI-001 Doctor** — Phase 1 implemented: deterministic read-only platform,
    service, connector, state-history, and operations diagnostics. Phase 2
    should add explicitly declared safe connector doctor adapters.
