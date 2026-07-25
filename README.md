# ITP — Infrastructure Telemetry Platform

**A self-hosted platform that turns infrastructure telemetry into an immediate, explainable operational view.**

[![Project status](https://img.shields.io/badge/status-Alpha%202-orange)](docs/releases/v0.2.0-alpha.2.md)
[![Validation](https://github.com/thehallifax/itp/actions/workflows/validate.yml/badge.svg)](https://github.com/thehallifax/itp/actions/workflows/validate.yml)
![Python](https://img.shields.io/badge/python-%E2%89%A53.9-blue)
![Virtualisation](https://img.shields.io/badge/virtualisation-VMware%20%7C%20Hyper--V%20%7C%20Proxmox-5b5bdb)

- Deploy with one guided setup command
- Collect network, security, compute, and virtualisation telemetry
- Open Grafana with managed dashboards already installed
- Turn evidence into service health, issues, risks, and recommendations
- Operate single sites, multi-site estates, and isolated customer deployments

> ITP is Alpha software intended for evaluation and controlled deployments.

## What is ITP?

ITP gives infrastructure teams one place to collect, normalise, and understand
telemetry that would otherwise be split across vendor portals, SNMP tools, and
unrelated dashboards.

It helps answer the practical questions operators face every day: What is
offline? Which service is affected? Is telemetry current? What needs attention
first? The platform keeps vendor detail available for investigation while
presenting a consistent operational view across the estate.

ITP is designed for IT administrators, infrastructure engineers, MSP
consultants, and technical teams managing schools, businesses, labs, or
multi-customer environments.

## Features

### Deployment

- Guided `./itp setup` experience
- Start, stop, restart, logs, status, and Doctor commands
- Automatic InfluxDB and Grafana provisioning
- Single-site, multi-site, estate, and customer-isolated profiles

### Telemetry

- SNMP discovery and collection
- Native read-only vendor integrations
- Canonical inventory, availability, performance, interface, and service data
- Deterministic freshness, state history, and change tracking

### Dashboards

- Infrastructure Overview as the default Grafana landing page
- Operations Wallboard and Collector Health
- Automatically installed dashboard packs for enabled connectors
- Stable managed dashboards without affecting user-created content

### Notifications

- Console and generic webhook delivery
- Critical, warning, informational, and recovery events
- Deduplication, acknowledgement, and repeat suppression
- Secret-safe delivery records

### Operations

- Vendor-neutral Service Health
- Deterministic issues, risks, and recommendations
- Continuous scheduled collection with daemon health
- Read-only diagnostics through ITP Doctor

### Supported platforms

- InfluxDB 3, Grafana, Telegraf, and Docker Compose
- VMware vSphere
- Microsoft Hyper-V
- Proxmox VE
- Linux, macOS, and Windows operator workflows

## Screenshots

| Infrastructure dashboard | Platform status |
| --- | --- |
| ![ITP infrastructure dashboard](docs/images/hero-dashboard.png) | ![ITP status command](docs/images/status-cli.png) |

| Doctor diagnostics | Guided setup |
| --- | --- |
| ![ITP Doctor command](docs/images/doctor-cli.png) | ![ITP setup wizard](docs/images/setup.png) |

> Screenshot placeholders will be replaced with current release captures.

## Quick Start

### Prerequisites

- Git
- Python 3.9 or later
- Docker Desktop or Docker Engine
- Docker Compose v2

### 1. Clone

```sh
git clone https://github.com/thehallifax/itp.git
cd itp
```

### 2. Run setup

```sh
./itp setup
```

The wizard creates local configuration, checks prerequisites, and offers to
start the platform. ITP creates and maintains its project-local Python
environment automatically.

On Windows PowerShell:

```powershell
.\itp.ps1 setup
```

### 3. Start ITP

```sh
./itp start
```

Running this command again is safe. It also provisions the datasource and
dashboard packs required by the enabled connectors.

### 4. Check the deployment

```sh
./itp doctor
./itp status
```

### 5. Open Grafana

Visit [http://localhost:3000](http://localhost:3000), or use the URL printed by
setup if you selected another port.

Next, add connector credentials under `secrets/`, enable the required
connectors, and run `./itp restart`. See the
[Quick Start guide](docs/quick-start.md) for the complete first-deployment
walkthrough.

### Try the isolated demo

To explore ITP without connecting production infrastructure:

```sh
./itp demo
```

The command starts a separate `itp-demo` Compose project, installs the managed
dashboard packs, and seeds 30 days of repeatable telemetry. Open
[http://localhost:3300](http://localhost:3300). It does not write to the root
deployment or its InfluxDB volume. See the [Demo Environment](docs/demo.md)
guide for data coverage and cleanup.

On Windows PowerShell, use `.\itp.ps1 demo`.

## Supported Connectors

| Connector | Coverage |
| --- | --- |
| SNMP | Discovery, switches, access points, printers, UPS, NAS, and generic infrastructure |
| Juniper Mist | Network and wireless telemetry through the Mist API |
| FortiGate | Firewall, system, performance, and interface telemetry |
| Palo Alto PAN-OS | Firewall, security, interface, licensing, and content telemetry |
| VMware vSphere | Managers, clusters, hosts, workloads, storage, and networks |
| Microsoft Hyper-V | Hosts, workloads, storage, and virtual networks |
| Proxmox VE | Clusters, nodes, virtual machines, containers, and storage |

Connector availability and setup maturity are recorded in the
[connector registry](docs/connector-registry.md). Supported connectors ship
with ITP and are enabled through configuration—no code changes are required.

## Documentation

- [Quick Start](docs/quick-start.md)
- [Demo Environment](docs/demo.md)
- [Deployment](docs/deployment.md)
- [Provisioning](docs/provisioning.md)
- [Operator Guide](docs/operator-guide.md)
- [Connector Registry](docs/connector-registry.md)
- [Dashboard Platform](docs/dashboard-platform.md)
- [Notifications](docs/notifications.md)
- [ITP Doctor](docs/doctor.md)
- [Multi-customer Profiles](docs/deployment-profiles.md)
- [Architecture](docs/architecture.md)
- [Upgrading](docs/UPGRADING.md)
- [Security Policy](SECURITY.md)

## Roadmap

Current priorities are:

- Safe update, backup, and restore workflows
- Broader and deeper connector telemetry
- Additional curated dashboard packs
- Operational history and lifecycle improvements
- Cross-platform release packaging, performance, and security hardening

See the [project roadmap](ROADMAP.md), [technical roadmap](docs/roadmap.md),
and [changelog](CHANGELOG.md) for milestone detail.

## Contributing

Bug reports, documentation improvements, tests, connector enhancements, and
dashboard contributions are welcome. Please read
[CONTRIBUTING.md](CONTRIBUTING.md) before opening an issue or pull request, and
never include credentials, customer data, or generated runtime state.

## License

License terms have not yet been published in this repository. Until a license
file is added, do not assume permission to use, modify, or redistribute the
project outside applicable copyright law.
