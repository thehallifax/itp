# Virtualisation intelligence

ITP provides one read-only operational model for VMware vSphere, Microsoft
Hyper-V and Proxmox VE. It normalises managers, clusters, hosts, virtual
machines, Proxmox containers, storage, networks and snapshots while retaining
provider-native evidence.

This feature cannot create, delete, start, stop, migrate or reconfigure
workloads, and it never performs snapshot remediation.

Virtualisation endpoints belong to one deployment profile and canonical site.
A deployment with no provider remains unaffected. Fixed rules flag disconnected
hosts, degraded clusters, capacity pressure, unhealthy guest integration and
old or excessive snapshots. Missing optional data produces Unknown or no
finding rather than an invented failure.

```bash
./itp profile virtualisation example-school --fixture vmware
./itp profile virtualisation example-school --fixture hyperv
./itp profile virtualisation example-school --fixture proxmox
./itp profile virtualisation-status example-school
```

Outputs are below `runtime/<profile>/virtualisation/`. See the
[schema](schema/virtualisation.md) and individual collector guides.
