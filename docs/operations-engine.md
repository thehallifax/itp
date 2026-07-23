# Operations Engine

The Operations Engine converts inventory, lifecycle state, collector run history,
and explicit operational signals into deterministic issues, risks, and
recommendations. It is an offline reasoning layer, not an alerting system. It
contains no machine learning, language model, remote API, or probabilistic score.

## Architecture and rule flow

`OperationsEngine` loads `runtime/inventory/assets.json`, `source_runs.json`,
`reconciliation.json`, and the optional `runtime/operations/signals.json` input.
It evaluates every registered `Rule`, deduplicates stable item IDs, orders each
collection by priority, and atomically writes:

- `runtime/operations/operations.json`
- `runtime/operations/operations.csv`
- `runtime/operations/dashboard/infrastructure-overview.json`

Grafana provisions the generated dashboard copy. The source dashboard under
`dashboards/Infrastructure Overview/` remains a version-controlled template.
Evaluation runs every five minutes by default and can be run immediately with:

```sh
docker compose exec collector python -m collectors operations generate
docker compose exec collector python -m collectors operations rules
```

## Output schema

```json
{
  "generated_at": "2026-07-23T00:00:00Z",
  "issues": [],
  "risks": [],
  "recommendations": []
}
```

Every item includes a stable ID, rule ID, kind, title, category, severity,
priority, device, site, summary, impact, reason, suggested action, recommended
action, and structured evidence. Supported categories and severities are enforced
by `analysis/operations/models.py`.

## Priority scoring

Priority is the severity base plus a rule-specific fixed weight, clamped to
0–100: Critical 90, High 75, Medium 55, Low 35, Info 15. A rule may add a small,
documented deterministic weight such as consecutive collector failures. No
historical training, randomness, or inferred probability is used.

## Signals

Rules that need telemetry not represented in inventory consume an optional local
`signals.json`. Supported initial keys are `approved_firmware`, `certificates`,
`printer_consumables`, and `wan`. Missing signals produce no finding; they never
produce fabricated data.

## Adding a rule

Subclass `Rule`, assign a unique `id` and supported `category`, and implement
`evaluate(context)`. Subclasses register automatically. Return `OperationalItem`
instances using the shared item helper so IDs, validation, scoring, and evidence
remain consistent. Add fixed-input tests proving repeatability and thresholds.

## Initial rules

The first rule set covers collector overdue/failure, generic device offline,
inventory drift, unsupported firmware, certificate expiry, printer consumables,
WAN packet loss/unavailability, unknown inventory, lifecycle age, and explicit AP,
switch, and firewall availability.

## Roadmap

Future work may add vendor-neutral adapters for service checks, backup state,
authentication failures, storage health, redundancy, and approved configuration.
Those adapters should only provide explicit signals; deterministic rules remain
responsible for all conclusions and priority calculations.
