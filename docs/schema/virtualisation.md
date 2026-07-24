# Canonical virtualisation schema

Schema version: `1`

## Purpose

Provide a collector-independent contract for virtualisation inventory, health
and governance.

Every object contains tenancy, provider, stable source and canonical IDs, name,
endpoint, timestamps, confidence, evidence and tags. Kinds cover managers,
clusters, hosts, VMs, containers, storage, networks, snapshots, alarms and
capacity. Providers are `vmware`, `hyperv` and `proxmox`.

Power states are `running`, `stopped`, `paused`, `suspended`, `saved` and
`unknown`; native states remain evidence.

Measurements:

- `virtualisation_platform`
- `virtualisation_cluster`
- `virtualisation_host`
- `virtualisation_workload`
- `virtualisation_storage`
- `virtualisation_snapshot`
- `virtualisation_finding`
- `virtualisation_collection`

Tags contain bounded tenancy, provider, relationship, type, state and severity
values. Names, messages, descriptions and address lists are fields.

## Tags and fields

Common tags are `deployment_id`, `site_id`, `provider`, manager, cluster and
host IDs, workload type, state and severity. Fields contain names, capacities,
utilisation, evidence and diagnostic text.

## Example

`virtualisation_workload` can describe a running Proxmox QEMU VM with four
vCPUs, while retaining `qemu-100` only as source evidence.

## Supported collectors

VMware, Hyper-V and Proxmox provider collectors.
