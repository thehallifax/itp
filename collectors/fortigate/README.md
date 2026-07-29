# FortiGate API collector

The native collector uses read-only FortiOS HTTPS monitor endpoints for system status,
resource usage, interfaces, and HA when supported. System status is required; optional
endpoint failures produce a partial health record instead of discarding valid data. TLS
verification is enabled by default.

The runtime onboarding prompt accepts a hostname with an optional port
(`firewall.example.invalid:8443`) or an HTTPS origin. It stores the canonical
`collectors.fortigate.host` value and matching `FORTIGATE_HOST` once. HTTP and
API paths such as `/api/v2` are rejected. The API token prompt is hidden.

```sh
cp secrets/fortigate.env.example secrets/fortigate.env
# Set host, token, customer and site; retain FORTIGATE_VERIFY_TLS=true in production.
```

Enable `collectors.fortigate` with `execution: edge`, then run:

```sh
ITP_RUNTIME_MODE=edge docker compose run --rm collector python -m collectors inspect fortigate
ITP_RUNTIME_MODE=edge docker compose run --rm collector python -m collectors collect fortigate
ITP_RUNTIME_MODE=edge docker compose up -d --force-recreate collector
docker compose logs --since=5m collector
```

Normalized output is written to `infrastructure_device`, `network_interface`, and
`security_appliance`; `collector_health` describes every run. The collector temporarily
dual-writes `fortigate_system`, `fortigate_performance`, and `fortigate_interfaces` for
dashboard compatibility. Telegraf SNMP remains available as fallback and counter
enrichment. Tokens are never included in diagnostics.
