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
5. **OPS-008 Phase 2 Scheduling and History Access** — schedule capture after
   canonical generation, define bounded retention/recovery, and expose history
   queries without treating changes as alerts.
6. **Health Scoring** — calculate explainable, vendor-neutral infrastructure
   health from normalized telemetry.
7. **Relationship Engine** — model site, device, interface, and service
   relationships without collector coupling.
8. **Additional collectors** — add supported vendors through the existing
   registry, configuration, inventory, and writer contracts.
