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
4. **Health Scoring** — calculate explainable, vendor-neutral infrastructure
   health from normalized telemetry.
5. **Relationship Engine** — model site, device, interface, and service
   relationships without collector coupling.
6. **Additional collectors** — add supported vendors through the existing
   registry, configuration, inventory, and writer contracts.

## Known defects

### BUG-001 — Scheduler locks require an active event loop

**Status:** Open  
**Priority:** High  
**Component:** `collectors/scheduler.py`

`Scheduler.__init__()` currently constructs `asyncio.Lock()` instances before an event loop is necessarily running.

This causes scheduler construction to fail with:

`RuntimeError: There is no current event loop in thread 'MainThread'.`

Affected tests:

- `test_scheduler_honours_collector_interface`
- `test_scheduler_lifecycle_overlap_and_interval`
- `test_scheduler_updates_health_file`

Required remediation:

- Do not create asyncio synchronization primitives during synchronous object construction.
- Lazily create per-collector and lifecycle locks from within an active event loop.
- Preserve overlap protection for collector execution and lifecycle updates.
- Confirm the full discovery test suite passes under supported Python versions.
