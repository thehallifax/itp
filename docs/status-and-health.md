# Status and Health State

ITP projects status from current runtime evidence. Historical failures remain
available for audit, but do not determine current health after a successful
recovery.

## Authoritative sources

| Projection | Canonical source |
| --- | --- |
| Connector execution | Latest connector entry in `runtime/pipeline-runs/` for operator commands; latest `last_run` in `runtime/inventory/source_runs.json` for canonical analysis |
| Connector freshness | Age of the latest completed connector execution |
| Scheduler lifecycle | `runtime/scheduler/state.json` |
| Capability collection | Capability manifest generated from current scheduler and source-run state |
| Infrastructure collector health | Signal adapters over the current source run |
| Service health | Current infrastructure, operations, capability, and signal projections |
| Deployment readiness | Readiness engine over current capability and collector projections |

The scheduler runs capability, infrastructure, operations, service-health, and
wallboard generation in that order. Dashboard code consumes those projections
and must not reinterpret historical errors.

After service health is written, the dashboard registry refreshes only the
state-derived platform dashboards: Operations Wallboard, Infrastructure
Overview, and Collector Health. Connector dashboards remain static managed
templates unless their pack or enablement changes.

## Current and historical fields

Current fields are replaced or cleared by every applicable execution:

- `status` and `health`
- `last_*_outcome`
- `last_error_class` and `last_safe_error_summary`
- `last_skip_reason`
- `consecutive_*_failures`

Historical audit fields are retained after recovery:

- `last_failure`
- `last_failed_run`
- `last_skipped_run`
- bounded source-run and PipelineRun history

A successful collection clears that connector phase's active error and skip
state. A successful discovery does the same for discovery. Scheduler root
errors are the deterministic aggregate of current failed connector phases and
clear only when every active connector phase has recovered. Historical
timestamps remain.

## Health and freshness

Health and freshness are independent:

- Health: `Healthy`, `Warning`, `Failed`, or `Disabled`.
- Freshness: `Fresh`, `Aging`, or `Stale` when a run exists. `Never Run`,
  `Unknown`, and `Disabled` remain explicit bootstrap states.

A recent failed execution is `Fresh` and `Failed`. A successful result that has
not run within its freshness policy is `Stale` and `Healthy`. Service health
continues to use the canonical service vocabulary (`Not Enabled`, `Unknown`,
`Healthy`, `Warning`, and `Critical`); readiness supplies the operator-facing
pending and unavailable distinctions.

## Manual collection

`itp collect` and `itp collector run` create authoritative PipelineRun
evidence, so connector status immediately reflects their latest result.
Manual execution does not rewrite daemon or scheduler lifecycle state because
those fields describe the continuous scheduler. Canonical infrastructure,
operations, and service-health projections refresh on the next analysis cycle;
their `generated_at` timestamps identify that snapshot boundary. This avoids
claiming scheduler recovery for work the scheduler did not perform.
