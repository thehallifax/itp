# Collectors

Each reference collector publishes a versioned support and runtime-evidence
contract. See [Collector capability manifests](collector-capabilities.md).

| Collector | Placement | Credentials | Canonical output |
| --- | --- | --- | --- |
| Mist | central | `secrets/mist.env` | device, availability, performance, wireless, collector_health |
| FortiGate API | edge | `secrets/fortigate.env` | device, availability, performance, interface, firewall, collector_health |
| SNMP/Telegraf | edge | local configuration | device, availability, performance/interface/wireless where applicable |
| Palo Alto PAN-OS | edge | `secrets/paloalto.env` | device, availability, performance, interface, firewall, license, content_package, collector_health |
| PaperCut MF | central | `secrets/papercut.env` | device, availability, performance, license, collector_health |
| HPE Aruba Networking Central | central | `secrets/aruba.env` | device, availability, wireless, collector_health |
| VMware vSphere | profile | profile-scoped `vmware.env` | virtualisation manager, cluster, host, workload, storage and findings |
| Microsoft Hyper-V | profile | optional profile-scoped `hyperv.env` | virtualisation host, workload, storage and findings |
| Proxmox VE | profile | profile-scoped `proxmox.env` | virtualisation manager, cluster, host, workload, storage and findings |

The authoritative support and onboarding metadata is available through:

```sh
python -m collectors connectors list
```

See the [connector registry architecture](connector-registry.md). Registry
support metadata does not enable a connector or replace runtime configuration.

Collectors are enabled only in `discovery/config.yml`. `ITP_RUNTIME_MODE` defaults to
`central`. Use `python -m collectors list`, `inspect NAME`, and `collect NAME` for
registration, safe inspection, and one-shot collection.

See [Measurement contracts](measurement-contracts.md) for required tags,
fields, update frequencies, and clean-database validation.
