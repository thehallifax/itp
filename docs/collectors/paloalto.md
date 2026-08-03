# Palo Alto Networks collector

The `paloalto` collector reads directly from a PAN-OS firewall through the HTTPS
XML API. It is read-only and performs synchronous `type=op` requests only.
It has no edge-agent, local shell, SNMP, or Telegraf dependency and therefore
supports both `central` and `edge` runtime placement. The selected runtime must
have network access to the PAN-OS management API.

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

Enable `collectors.paloalto` in the selected deployment's ignored
`runtime/deployments/<deployment>/collectors.yml`, using that canonical ID for
`site`. WAN classification is operator-owned and explicit:

```yaml
wan_interfaces:
  - name: ethernet1/5
    role: primary
    display_name: Primary Internet
  - name: ethernet1/6
    role: backup
    display_name: Backup Internet
```

Valid roles are `primary`, `secondary`, `tertiary`, `backup`, `cellular`,
`mpls`, `internet`, and `other`. Names must exist in the discovered PAN-OS
interfaces. Duplicate names and multiple primary roles are rejected. With no
configuration, Internet health remains Unknown; interface numbers, names,
traffic, and default routes never imply a WAN role.

## Commands

```sh
./itp collector test paloalto --deployment <deployment>
./itp collector run paloalto --deployment <deployment>
./itp status --deployment <deployment>
./itp doctor --deployment <deployment>
./itp dashboard generate --deployment <deployment>
./itp logs collector --deployment <deployment>
```

Identity is mandatory. HA, interfaces, resources and licences are optional:
capability-specific failure produces partial health while retaining the asset.

Operational commands and parsed paths:

| Command | Confirmed PAN-OS paths |
|---|---|
| `show system info` | `system/*`, content `*-version`/`*-release-date`, `device-certificate-status` |
| `show high-availability state` | `enabled`, `group/local-info/state`, `group/peer-info/state` |
| `show interface all` | `ifnet/entry/{name,state,speed,duplex}` |
| `show counter interface all` | `entry/{ibytes,obytes,ipackets,opackets,ierrors,idrops,tx-error}` |
| `show system resources` | top CPU idle and memory total/used |
| `show running resource-monitor second last 1` | per-core `coreid/value`, packet-buffer `name/value` |
| `show session info` | `num-active`, `num-max`, `num-tcp`, `num-udp` |
| `request license info` | `licenses/entry/{feature,status,expires,expired}` |

Interface counters remain cumulative. The collector derives rates and discards
negative deltas after a reboot or counter reset. It publishes
canonical `rx_bps` and `tx_bps` fields after comparing successive observations.
The first observation establishes a baseline, so a new process or deployment
may require two successful collections before throughput appears. Duplicate
timestamps, missing counters and counter resets produce no rate rather than a
fabricated or negative value. Optional missing counters do not fail collection.
Both the Operations Wallboard and Palo Alto Operational Overview query these
canonical `interface` rate fields across the selected Grafana time range. Chart
density therefore follows the collector cadence. Missing observations remain
gaps rather than being converted to zero or connected across a long outage.

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

## Licence, content, and service-health semantics

Expired licences have `remaining_days=0` and a positive `expired_days`;
perpetual and malformed values are distinct states. Content timestamps use
the firewall-provided AWST timezone where present. Unknown timestamps omit age
instead of becoming zero.

Internet health uses only configured WAN interfaces. Interface Inventory and
Interface Status use the canonical `interface` measurement; an unavailable
interface endpoint is reported as Feature unavailable rather than awaiting a
first collection. All configured uplinks
down is Critical; primary down with a working backup is Warning; stale,
missing, invalid, or unconfigured evidence is Unknown. Security health uses
firewall availability, subscription expiry, and device-certificate state.
Content age does not become Critical without an explicit policy.

`wan_interfaces[].name` is the stable PAN-OS interface identifier used in
telemetry tags and dashboard filters. `display_name` is presentation metadata
used in titles only; it is never substituted into an `interface_name` SQL
predicate. To inspect names before configuration, run a collection
and open Interface Inventory, or inspect the shared collector diagnostics:

```sh
./itp collector run paloalto --deployment <deployment>
./itp logs collector --deployment <deployment>
```

## Dashboard regeneration

```sh
./itp dashboard generate --deployment <deployment>
./itp restart --deployment <deployment>
```

The dashboard consumes canonical `device`, `firewall`, `performance`,
`interface`, `license`, `content_package`, and `collector_health`
measurements. PAN-OS 10.2 does not currently expose a trustworthy
`tx_discards_total` or `link_flaps_total` in the confirmed command output, so
those fields remain absent.
