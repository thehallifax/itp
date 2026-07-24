# Hyper-V collector

Hyper-V collection consumes the versioned JSON emitted by
`Collect-ITPHyperV.ps1`. The script performs read-only Hyper-V and optional
FailoverClusters queries.

Transport contracts cover local PowerShell, explicit PowerShell remoting and
fixtures. The Compose host is not assumed to run Hyper-V cmdlets. Use delegated
read permissions; Domain Administrator is unnecessary. ITP does not enable
CredSSP, unconstrained delegation or insecure remoting, and does not silently
work around double-hop restrictions.

Store optional remoting credentials in profile-scoped `hyperv.env`. Missing
modules or limited permissions become diagnostics.
