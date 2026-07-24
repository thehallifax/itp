# Site hierarchy and estate aggregation

`parent_id` references another canonical site in the same profile. Hierarchy is
optional. `rollup_group` is an optional bounded label and never crosses the
profile boundary.

Ordering is deterministic: roots first, then children by depth; within a level,
`display_order` precedes display name. Disabled sites are excluded from estate
evaluation.

Validation rejects duplicate IDs or aliases, unknown or disabled parents,
self-parenting, cycles, unsupported types, invalid rollup groups, excessive
depth and duplicate sibling display order.

## Estate service rules

- **Critical:** an applicable site is Critical, or a configured central provider is Critical.
- **Warning:** no site is Critical and one is Warning; mixed Healthy and Unknown is Warning.
- **Healthy:** every applicable enabled site is Healthy.
- **Unknown:** applicable evidence is insufficient.
- **Not Enabled:** no enabled site provides the capability.

Rollups record confidence, affected and total site counts, affected site IDs,
evidence and evaluation time. Dependencies are never inferred:

```yaml
dependencies:
  - service: identity
    provider_site_id: head-office
    consumer_site_ids: [campus-one, campus-two]
```

A critical provider produces one estate result identifying its provider and
consumers. Site views include only findings explicitly attributed to that site.

Virtualisation follows the same rule. Manager `Unknown` does not propagate.
Only a configured dependency whose service matches a confirmed Critical
virtualisation service (for example Shared Storage) can add consumer sites to
the estate impact. Manager, cluster, host, workload and storage relationships
use canonical IDs and never display names.
