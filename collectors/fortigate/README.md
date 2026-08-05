# FortiGate API collector

The native collector uses read-only FortiOS HTTPS monitor endpoints for system status,
resource usage, interfaces, and HA when supported. System status is required; optional
endpoint failures produce a partial health record instead of discarding valid data. TLS
verification is enabled by default.

ITP runtime images include Debian's maintained public CA trust store. A
deployment-specific private CA loaded with the command below augments those
public roots; it does not replace them.

```sh
./itp credentials ca add <certificate.pem> --deployment <deployment>
```

For a publicly issued certificate, install the full-chain certificate on the
FortiGate. Do not normally import public roots or intermediates into ITP.
Diagnostics distinguish expiry, hostname mismatch, incomplete public chains,
untrusted private CAs, and generic trust failures. `verify_tls: false` is a
diagnostic-only escape hatch and is not recommended for production.

For example, if a FortiGate presents only its wildcard leaf and that leaf is
issued by Let's Encrypt, install the full chain (including the required public
intermediate) on the FortiGate. Do not import Let's Encrypt roots or
intermediates into ITP. The private-CA command above is only for internal PKI.

The runtime onboarding prompt accepts a hostname with an optional port
(`firewall.example.invalid:8443`) or an HTTPS origin. It stores the canonical
`collectors.fortigate.host` value and matching `FORTIGATE_HOST` once. HTTP and
API paths such as `/api/v2` are rejected. The API token prompt is hidden.

```sh
cp secrets/fortigate.env.example secrets/fortigate.env
# Set host, token, customer and site; retain FORTIGATE_VERIFY_TLS=true in production.
```

FortiGate can run in either central or edge mode. Central mode is appropriate
when the collector service can reach the management HTTPS endpoint; edge mode
remains available for private networks that require local placement. Then run:

```sh
docker compose run --rm collector python -m collectors inspect fortigate
docker compose run --rm collector python -m collectors collect fortigate
docker compose up -d --force-recreate collector
docker compose logs --since=5m collector
```

Normalized output is written to `infrastructure_device`, `network_interface`, and
`security_appliance`; `collector_health` describes every run. The collector temporarily
dual-writes `fortigate_system`, `fortigate_performance`, and `fortigate_interfaces` for
dashboard compatibility. Telegraf SNMP remains available as fallback and counter
enrichment. Tokens are never included in diagnostics.

Guided setup uses the existing read-only inspection, presents interface role,
alias, status and SD-WAN evidence where returned, and requires explicit WAN
selection:

```sh
./itp collector setup fortigate --deployment <deployment>
```

Recommendations never silently classify an interface. The canonical interface
name and friendly display label remain separate.
