# VMware collector

The read-only VMware boundary targets vCenter-managed estates. Standalone ESXi
is supported only after its API and permissions are certified. Inventory covers
manager identity, clusters, hosts, VMs, datastores, networks and snapshots.

Use a least-privilege vSphere account. Store `VMWARE_USERNAME` and
`VMWARE_PASSWORD` in profile-scoped `vmware.env`. TLS verification defaults on;
use a CA bundle for private authorities. Credentials, sessions and headers are
never logged.

The REST session boundary exists, but complete live inventory mapping remains
disabled until certified against the target vCenter release. Fixture mode
exercises the complete canonical pipeline.
