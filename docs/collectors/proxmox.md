# Proxmox VE collector

The Proxmox boundary uses the read-only REST API and prefers an API token. It
keeps QEMU virtual machines and LXC containers distinct and collects nodes,
storage, networks, HA, replication and snapshots.

Store `PROXMOX_TOKEN_ID` and `PROXMOX_TOKEN_SECRET` in profile-scoped
`proxmox.env`. Tokens, tickets, CSRF values, cookies and authorization headers
are never rendered. TLS verification defaults on; use a custom CA bundle for
private certificates.

Authenticated read-only access is implemented. Complete live contract mapping
must be certified before live mode is enabled; fixture mode covers clustered,
standalone-style and partial evidence.
