# Palo Alto Networks collector

The `paloalto` collector reads directly from a PAN-OS firewall through the HTTPS
XML API. It is read-only and performs synchronous `type=op` requests only.

## Supported scope

- System identity, model, serial, PAN-OS version, management address and uptime
- Installed content versions returned by `show system info`
- HA state: standalone, healthy, degraded or unavailable
- Physical, aggregate and logical interface state
- A bounded resource snapshot where the PAN-OS release is safely parsable
- Licence/subscription status and expiry

COL-PA-001 does not write configuration, commit, restart, install software,
analyse policy, ingest logs, monitor GlobalProtect users, infer WAN roles or
physical topology, query public latest versions, or discover a Panorama fleet.

## Account, secret and TLS

Use an API administrator restricted to the operational commands above. It needs
no configuration, commit, policy, software, restart, or administrator-management
permission.

```sh
cp secrets/paloalto.env.example secrets/paloalto.env
chmod 600 secrets/paloalto.env
```

Set `PALOALTO_API_KEY` in that ignored file. The key is sent only through the
`X-PAN-KEY` header; it is never placed in a URL, tracked configuration, runtime
output, dashboard, log, or exception.

HTTPS and certificate verification default to enabled. For a private CA, mount
its PEM file and set `ca_bundle` to its container path. The explicit
`allow_insecure_http` option is development-only; installing the correct CA is
the production solution.

## Site and configuration

Add a canonical site to `config/sites.yml`:

```yaml
sites:
  - id: customer-site-slug
    display_name: Customer Site Name
    aliases:
      - CUSTOMER-SITE
```

Enable `collectors.paloalto` in `discovery/config.yml`, using that canonical ID
for `site`. `expected_interfaces` is the only source of actionable interface-down
findings; other down interfaces remain neutral observations.

## Commands

```sh
docker compose exec collector python -m collectors paloalto validate
docker compose exec collector python -m collectors paloalto discover
docker compose exec collector python -m collectors paloalto run
docker compose exec collector python -m collectors sites generate
docker compose exec collector python -m collectors infrastructure generate
docker compose exec collector python -m collectors operations generate
docker compose exec collector python -m collectors wallboard generate
```

Identity is mandatory. HA, interfaces, resources and licences are optional:
capability-specific failure produces partial health while retaining the asset.

Local read-only smoke test:

```sh
export PALOALTO_URL='https://192.0.2.1'
export PALOALTO_API_KEY='REDACTED'
curl --silent --show-error --fail \
  --header "X-PAN-KEY: ${PALOALTO_API_KEY}" \
  --get "${PALOALTO_URL%/}/api/" \
  --data-urlencode 'type=op' \
  --data-urlencode 'cmd=<show><system><info></info></system></show>'
```

Do not paste the key into chat or commit it. For self-signed diagnosis, prefer
`curl --cacert`; reserve `-k` for isolated temporary diagnostics.

## Canonical behavior and limitations

One queried firewall creates one canonical `firewall` asset with vendor
`Palo Alto Networks`. Identity precedence is serial, hostname, management IP,
then deterministic source ID. SNMP data with the same serial fuses into it. An
HA peer serial is evidence only and never creates a phantom asset.

Collector failure affects Observability Health and does not assert firewall
offline state. Authoritative evidence can produce API, degraded-HA,
expected-interface, and licence findings. Content versions are inventory only;
no outdated claim is made without an approved baseline.

Safe troubleshooting categories include `credential`, `permission`, `tls`,
`timeout`, `unreachable`, `unsupported`, `malformed_xml`, and `empty_result`.
