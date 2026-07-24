# Changelog

All notable changes to ITP are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added

- CLI-001 Phase 1 read-only Doctor with deterministic human/JSON reports,
  registry-driven connector readiness, offline/strict modes, secret redaction,
  service checks, and automation-safe exit codes.
- OOBE-001 Phase 1 cross-platform bootstrap wizard with safe template
  generation, Docker and port checks, Compose validation, optional startup,
  service-health waiting, and unattended operation.
- OOBE-002 Phase 1 declarative connector registry, deterministic inspection
  CLI, validated maturity and secret-handling metadata, OOBE integration, and
  contributor completion contract.
- OPS-008 Phase 1 canonical snapshots, deterministic field-level change
  detection, stable change identifiers, atomic filesystem storage, and
  config-independent CLI processing.
- OPS-008 Phase 2 opt-in pipeline capture with explicit completeness,
  source/site isolation, removal suppression, atomic latest-pointer rollback,
  idempotent run results, and capture inspection CLI.

### Planned

- OPS-008 Phase 3 bounded retention, history queries, and operational
  state-transition inputs.

## 0.2.0-alpha.2

### Added

- Canonical infrastructure, asset, site, estate, and virtualisation models.
- Read-only VMware vSphere, Microsoft Hyper-V, and Proxmox VE support.
- Deterministic Operations Engine for issues, risks, and recommendations.
- Vendor-neutral Service Health with site and estate evaluation.
- Capability-aware dashboard registry and managed Grafana provisioning.
- Exception-driven Operations Wallboard and deterministic release evidence.
- Multi-customer deployment profiles and multi-site estate rollups.

### Changed

- Operational dashboards consume canonical state instead of embedding
  vendor-specific health policy.
- Virtualisation findings now use conservative, evidence-based service-impact
  propagation.

### Known limitations

- Canonical operational state is file-backed and has no durable history.
- Collector, provider, and service-domain coverage remains incomplete.
- ITP remains Alpha software and is not represented as production-ready.

## 0.2.0 - 2026-07-23

### Added

- Operations-first Grafana hierarchy and Infrastructure Overview.
- Deterministic operational intelligence with JSON and CSV outputs.
- Scheduled operational evaluation and operational documentation.

### Changed

- Vendor dashboards moved beneath the Vendor folder.
- Grafana provisioning expanded to operational folders.

### Fixed

- Scheduler construction no longer requires an active asyncio event loop.

## 0.1.0 - 2026-07-22

### Added

- Initial collector framework, SNMP discovery, Mist and FortiGate collectors.
- Inventory, lifecycle, canonical telemetry, Grafana, and Docker foundations.
