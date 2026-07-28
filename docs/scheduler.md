# Scheduler lifecycle

ITP uses one asyncio scheduler for native collector discovery and collection.
The collector service enters it through `python -m collectors`; `./itp daemon`
uses the same scheduler through the operator runtime.

Before OPS-010C, both entry points created discovery and collection loops in
the same task-creation pass. Each loop ran immediately and slept afterward.
Discovery and collection share a lazy per-collector `asyncio.Lock`, so normal
startup could log `skipped_overlap` when the second phase reached that lock.
Lifecycle and Operations have separate locks and retain their configured
interval calculations. Collector exceptions were isolated, while task
cancellation propagated through `asyncio.gather`; daemon heartbeat and
collection state were written under `runtime/daemon/`.

## Deterministic startup

Startup is intentionally sequential:

1. `starting`
2. `initial_discovery`
3. `initial_collection`
4. `ready`, or `degraded` after a recoverable collector failure
5. recurring discovery and collection

Initial collection runs only for collectors whose initial discovery succeeded.
A discovery failure records `prerequisite_unavailable` for the dependent
collection and enters `degraded`. A collection failure also enters `degraded`.
Both are recoverable: recurring schedules start so a later run can restore
health. Configuration and collector-construction failures occur before the
scheduler and remain startup errors.

Recurring deadlines are anchored to completion of the corresponding initial or
recurring run. Configured intervals are unchanged, and a long initial run
cannot cause an immediate catch-up tick.

## Overlap and shutdown

Per-collector locks continue to protect steady-state work. A rejected tick uses
a precise reason: `active_discovery`, `active_collection`, or
`shutdown_in_progress`. `prerequisite_unavailable` identifies an initial
collection that could not safely run.

Shutdown transitions through `stopping` to `stopped`. No new work starts after
shutdown begins, background tasks are cancelled and awaited, and cancellation
is not treated as a collector failure.

## Runtime state and diagnosis

The scheduler atomically writes:

```text
runtime/scheduler/state.json
```

It records lifecycle and readiness timestamps, initial outcomes, latest
attempt/success/outcome/duration for discovery and collection, next deadlines,
failure streaks, active phase and run ID, skips, and safe error classification.
All timestamps are UTC. Error messages and configuration values are not stored.

Use:

```sh
./itp status
./itp doctor
```

Doctor reports lifecycle, initial phase outcomes, recent successes, failure
streaks, skip reason, and stale runtime state. For support, retain the scheduler
state file, daemon state, daemon log, and relevant PipelineRun files; review
them for deployment-specific information before sharing.
