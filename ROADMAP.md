# Infrastructure Telemetry Platform (ITP) Roadmap

## Vision

Infrastructure Telemetry Platform (ITP) is an opinionated, self-hosted infrastructure telemetry platform that enables IT professionals to deploy a complete monitoring stack in minutes.

**Core Principles**

- Clone → Setup → Running
- Opinionated defaults
- Self-diagnosing
- Registry-driven
- Production-ready
- Extensible
- Community supported (best effort)
- No vendor lock-in

---

# v0.1 - Operator Foundation ✅

## Setup & Configuration

- [x] Setup Wizard
- [x] Profile Management
- [x] Connector Registry
- [x] Configuration Validation

## Operations

- [x] Doctor
- [x] Collect
- [x] Status
- [x] Daemon

## Runtime

- [x] Pipeline Runs
- [x] State History
- [x] Freshness Tracking
- [x] Notification Engine

---

# v0.2 - Deployment Experience 🚧

**Goal**

A brand-new deployment can be operational in under 10 minutes.

## Completed

- [x] Stack Lifecycle Commands
- [x] Automatic Provisioning
- [x] Docker Integration
- [x] InfluxDB Provisioning
- [x] Grafana Datasource Provisioning

## Remaining

- [ ] Dashboard Pack Provisioning
- [ ] Connector Dashboard Auto-Install
- [ ] Update Engine
- [ ] Backup
- [ ] Restore

---

# v0.3 - Connector Ecosystem

**Goal**

Provide production-quality telemetry for common enterprise infrastructure.

## SNMP

- [ ] Generic Devices
- [ ] Switches
- [ ] Printers
- [ ] UPS
- [ ] Environmental Sensors

## FortiGate

- [ ] Interfaces
- [ ] Sessions
- [ ] VPN
- [ ] SD-WAN
- [ ] Hardware Health

## Mist

- [ ] Access Points
- [ ] Clients
- [ ] Radios
- [ ] RF Utilisation
- [ ] Site Health

## VMware

- [ ] ESXi Hosts
- [ ] Datastores
- [ ] Virtual Machines
- [ ] Cluster Health

---

# v0.4 - Dashboard Experience

**Goal**

Every deployment includes useful dashboards without manual imports.

## Core

- [ ] Infrastructure Overview
- [ ] Runtime Health
- [ ] Collector Health
- [ ] Notification Summary
- [ ] Collection History

## Dashboard Packs

- [ ] SNMP
- [ ] FortiGate
- [ ] Mist
- [ ] VMware

---

# v0.5 - Automation

**Goal**

Reduce operational overhead through automation.

- [ ] Scheduled Updates
- [ ] Connector Auto-Discovery
- [ ] Configuration Validation
- [ ] Runtime Optimisation
- [ ] Automatic Cleanup
- [ ] Self-Healing Services

---

# v0.6 - Production Ready

**Goal**

Prepare ITP for long-term production use.

- [ ] Documentation Review
- [ ] Cross-Platform Testing
- [ ] Linux Installer
- [ ] Release Packaging
- [ ] Performance Optimisation
- [ ] Security Review

---

# Future Ideas

These are intentionally **not** part of the active roadmap.

## Possible Future Features

- Cloud Deployment
- Kubernetes
- High Availability
- Multi-Node Collection
- Plugin Marketplace
- Community Dashboard Packs

---

# Out of Scope

These are intentionally excluded to keep ITP focused.

- Governance & Compliance
- Asset Management
- Patch Management
- Remote Management (RMM)
- Ticketing
- AI Analysis
- Billing
- User Management
- Device Configuration Management

---

# Design Principles

## 1. Clone → Setup → Running

Deploy a working telemetry platform with minimal effort.

## 2. Opinionated Defaults

Provide sensible defaults that work for most deployments.

## 3. Self-Diagnosing

Doctor should explain problems and suggest fixes.

## 4. Registry-Driven

Connectors, dashboards and provisioning should rely on metadata rather than hardcoded logic.

## 5. Composable

- Connectors collect data.
- Runtime stores data.
- Dashboards visualise data.
- Notifications alert operators.

Each layer should remain independent.

## 6. Safe

- Never expose secrets.
- Idempotent operations.
- Preserve existing configuration.
- Fail safely.

## 7. Operational First

Every new feature should reduce the effort required to deploy, operate or maintain the platform.

---

# Current Milestone

🚧 Dashboard Experience

Focus areas:

- Dashboard Pack Registry
- Infrastructure Overview Dashboard
- Connector Dashboard Packs
- Automatic Dashboard Provisioning
