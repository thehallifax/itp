# Readiness and dashboard empty states

ITP generates one vendor-neutral readiness contract at:

```text
runtime/dashboard/readiness.json
```

It combines deployment/provisioning state, dashboard-registry enablement,
canonical collector run records, inventory presence and Operations output.
Dashboard generators embed its safe display labels and operator actions into
managed Grafana dashboards. Grafana UI edits and generic `No data` handling are
not readiness policy.

## Semantic states

| State | Meaning |
| --- | --- |
| `not_configured` | No relevant collector or capability is enabled. |
| `waiting_first_collection` | Collection is enabled, but no successful run or required downstream state exists yet. |
| `unavailable` | Collection was attempted and failed or became stale. |
| `healthy` | Current positive evidence exists without degradation. |
| `warning` | Current evidence exists with a deterministic degraded condition. |
| `critical` | Explicit critical infrastructure or service evidence exists. |

Missing telemetry is never Healthy. A disabled capability is not a failure, and
an enabled collector without a successful run is not reported as merely
Unknown.

Overall readiness uses one explicit highest-impact precedence:

```text
critical > unavailable > warning > waiting_first_collection
         > not_configured > healthy
```

This prevents a healthy inventory from hiding collector degradation while
retaining clean-install onboarding: two unconfigured subordinate states still
produce `not_configured`, and an enabled collector awaiting its first result
outranks an otherwise healthy platform component.

The readiness evaluator derives collector and onboarding states. Canonical
infrastructure evaluation supplies confirmed `warning` or `critical` asset
evidence, then uses the same precedence function to recompute overall
readiness. Dashboard renderers preserve that resulting state; they do not
downgrade it.

Each readiness record contains:

- `state` and stable `reason`;
- `configured`, `enabled`, and `first_run_completed`;
- `last_success` and `stale`;
- concise `display_label`;
- non-sensitive `operator_action`.

No credential name, value, secret path or internal exception is included.

## Onboarding

Infrastructure Overview embeds the canonical seven-step Setup Status:

1. Platform services running
2. Deployment configured
3. Collector credentials configured
4. At least one collector enabled
5. First successful collection completed
6. Infrastructure inventory available
7. Operational analysis available

Demo mode marks valid seeded demo stages complete and never presents the demo as
an unconfigured production deployment. The explicit demo flag is sufficient:
readiness does not require a synthetic enabled collector when seeded demo
outputs are active.

## Dashboard author contract

Dashboard authors should consume `readiness`, or the readiness fields embedded
in `infrastructure-summary.json` and `wallboard-summary.json`. Do not infer
health from a missing query result, asset count, vendor name or Grafana
`noValue` fallback. Empty tables must include an explanatory row.

A new collector participates automatically by:

1. declaring its enablement and capabilities in the existing connector and
   dashboard manifests;
2. writing canonical source-run state, including last run, last successful run
   and status;
3. producing canonical inventory when discovery succeeds.

No readiness-specific vendor branch is required.
