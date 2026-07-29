# Connector Registry

The connector registry is ITP's authoritative, user-facing catalogue of
implemented collection boundaries. It prevents setup, validation, doctor,
status, demos, and documentation from developing independent provider lists.

The declarative source is `collectors/connector-registry.yml`. The
side-effect-free API is `ConnectorMetadataRegistry` in
`collectors/connector_registry.py`.

## What the registry owns

For each connector it records:

- stable ID, aliases, name, and vendor;
- canonical infrastructure domains and supported deployment types;
- implementation maturity and configuration mode;
- required credential and configuration fields;
- secret templates and scope without secret values;
- setup, validation, doctor, status, and fixture maturity;
- documentation and an implementation source reference;
- limitations or manual-only notes.

Registry loading validates controlled vocabulary, IDs and aliases,
documentation, secret templates, and implementation source symbols. It does
not import vendor SDKs, instantiate collectors, read credentials, load runtime
configuration, or make network calls.

## Current inventory

| ID | Boundary | Domains | Maturity | Setup |
| --- | --- | --- | --- | --- |
| `snmp` | Multi-vendor SNMP and generated Telegraf polling | switching, wireless, printing, servers, power, environmental | supported | manual |
| `mist` | Juniper Mist API | switching, wireless | supported | manual |
| `fortigate` | Fortinet FortiGate API | firewall, internet, switching | supported | manual |
| `paloalto` | Palo Alto PAN-OS API | firewall, internet | supported | manual |
| `papercut` | PaperCut MF System Health API | printing, servers | supported | manual |
| `vmware` | VMware vSphere | virtualisation, servers | profile-only | profile manual |
| `hyperv` | Microsoft Hyper-V | virtualisation, servers | profile-only | profile manual |
| `proxmox` | Proxmox VE | virtualisation, servers | profile-only | profile manual |

No connector currently claims guided setup. OOBE-001 continues to bootstrap the
platform. OOBE-002 Phase 1 exposes accurate connector metadata; a later phase
may present only entries explicitly marked `guided_setup: true`.

Printer, UPS, environmental, server, and generic network evidence can be
collected through SNMP. PaperCut MF also has a dedicated read-only System
Health connector. UniFi and Aruba appear in future-facing service/dashboard
concepts but have no connector implementation and are therefore not advertised
in the registry. Operations
Engine and Service Health consume canonical outputs downstream; they are not
external connectors.

## Read-only inspection

```sh
python -m collectors connectors list
python -m collectors connectors list --json
python -m collectors connectors inspect pan-os --json
```

IDs and output ordering are deterministic. Aliases resolve to the stable ID.
These commands do not require `.env`, `discovery/config.yml`, secrets, Docker,
or runtime state.

## Connector completion contract

A contributor adding a connector must:

1. Add one registry entry with a stable ID and any migration aliases.
2. Declare only canonical domains the implementation actually emits.
3. Declare credential names and secret-file scope without values.
4. Provide an implementation reference and connector documentation.
5. Record setup, validation, doctor, status, and fixture maturity honestly.
6. Add canonical output tests and registry coverage.
7. Define site/domain state-history scope and explicit completeness semantics
   where the connector participates in canonical capture.
8. Add dashboard metadata separately only when a tested dashboard exists.

A connector is not fully integrated merely because collection code exists.
Unsupported capabilities remain `false`; they must not be inferred from other
connectors.

## Repository list audit

Replaced:

- `scripts/itp.py` secret requirements now come from connector credential
  metadata.
- OOBE setup reads the shared registry and no longer needs a future connector
  definition of its own.

Intentionally separate:

- `CollectorRegistry` is a runtime class factory for native scheduled
  collectors, not user-facing maturity metadata.
- `collectors.__main__._enabled_collectors` is runtime placement/dispatch and
  remains limited to collectors run by that scheduler.
- Virtualisation provider branches in `scripts/itp.py` select different
  transport clients and are functional dispatch.
- Dashboard manifests select dashboard packs and remain independent of
  connector onboarding maturity.
- Service definitions and capability vocabularies describe canonical
  downstream services, not collection implementations.
- Demo scenario names describe evidence scenarios, not supported connectors.
- Profile configuration keys are compatibility contracts and remain explicit.

Later phases can consume registry status and validation flags in new doctor and
guided-onboarding commands without refactoring these runtime dispatch tables.
