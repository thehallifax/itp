# Analysis

Vendor-neutral operational reasoning lives here. The first implementation is the
[deterministic Operations Engine](../docs/operations-engine.md), which produces
active issues, operational risks, and recommendations without ML or cloud
dependencies.

The [Canonical Service Health Engine](../docs/service-health.md) converts the
canonical infrastructure state and operations findings into capability-aware,
vendor-neutral service states. Dashboards remain consumers of these outputs and
do not own service-health policy.

This directory is reserved for vendor-neutral health scoring, lifecycle
tracking, change detection, and relationship analysis. No collector or Grafana
logic belongs here.
