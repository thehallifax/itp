# Changelog

## [0.2.0] - 2026-07-23

### Added

- Operations-first Grafana dashboard hierarchy
- Infrastructure Overview dashboard
- Deterministic operational intelligence engine
- Active Issues, Operational Risks, and Recommendations
- Fourteen automatically registered operational rules
- JSON and CSV operational outputs
- Scheduled operational evaluation
- Operational dashboard and engine documentation

### Changed

- Vendor dashboards moved beneath the Vendor folder
- Grafana provisioning expanded to operational folders
- Scheduler lock initialisation corrected
- Infrastructure Overview populated from generated operational findings

### Fixed

- BUG-001: Scheduler construction failed when no asyncio event loop was active

### Security

- Local credentials rotated after expanded Docker Compose configuration was exposed in diagnostic output


All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog.

## [0.1.0] - 2026-07-22

### Added

- Production repository foundation
- Unified collector framework
- Mist collector
- FortiGate collector
- SNMP discovery framework
- Inventory engine
- Lifecycle tracking
- Grafana dashboards
- Discovery service
- Canonical telemetry schema
- Documentation
- Installation scripts
- Docker deployment
- Versioning

### Changed

- Standardised Python project structure
- Moved secrets to example configuration files
- Introduced pyproject.toml
- Added development dependency management

### Known Issues

- BUG-001 — Scheduler lock initialisation requires an active asyncio event loop (3 tests currently failing).
