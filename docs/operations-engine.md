# Operations Engine

The Operations Engine converts canonical assets, lifecycle state, collector run
history, and explicit operational signals into deterministic issues, risks, and
recommendations. It is an offline reasoning layer, not an alerting system. It
contains no machine learning, language model, remote API, or probabilistic score.

Asset rules evaluate only fused Infrastructure State records. Their findings
carry `canonical_id` and source provenance, so multiple collector observations
of one device cannot create duplicate operational issues. Findings also carry
canonical `site_id`; `site` remains the registry display name for operators.

## Architecture and rule flow

`OperationsEngine` loads `runtime/infrastructure/state.json` and evaluates its
canonical assets, collector state, reconciliation data, and optional signals.
It evaluates every registered `Rule`, deduplicates stable item IDs, orders each
collection by priority, and atomically writes:

- `runtime/operations/operations.json`
- `runtime/operations/operations.csv`
- `runtime/dashboard/grafana/infrastructure-overview.json`

Infrastructure State runs first and provides the canonical asset view and flat
summary. Grafana provisions the generated dashboard copy. The source dashboard under
`dashboards/Infrastructure Overview/` remains a version-controlled template.
Evaluation runs every five minutes by default and can be run immediately with:

```sh
docker compose exec collector python -m collectors operations generate
docker compose exec collector python -m collectors operations rules
```

After Operations evaluation, the scheduler regenerates Operations Wallboard.
Its attention tables preserve deterministic priority ordering and show at most
five items for the current site scope.

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

PAN-OS rules require authoritative evidence: API failure changes observability,
degraded HA and configured expected-interface failures create issues, and
licence findings require API-returned expiry. Installed content versions alone
never produce currency findings.

## Roadmap

Future work may add vendor-neutral adapters for service checks, backup state,
authentication failures, storage health, redundancy, and approved configuration.
Those adapters should only provide explicit signals; deterministic rules remain
responsible for all conclusions and priority calculations.

## Virtualisation promotion

The formal virtualisation adapter consumes canonical objects, findings and
collection status. Operations schema version remains compatible; optional
fields add `domain`, provider/object/relationship IDs, confidence, source
finding ID, observation times and affected service IDs.

Severity is evidence-based: inaccessible shared storage and confirmed required
replication failure are Critical; standalone host loss is Critical; clustered
host loss, capacity pressure and governance findings are degraded conditions.
Management reachability, permission limitations, partial collection and stale
evidence remain Unknown when workload impact is not independently confirmed.
