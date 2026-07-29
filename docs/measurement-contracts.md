# Collector measurement contracts

Measurement availability is declared by the versioned
[collector capability manifest](collector-capabilities.md). A documented
measurement is not proof that the latest run collected it.

All profile-scoped points require `deployment_id`, `customer_id`, `site_id` and
`collector` tags. Device-scoped points additionally require `device_id` and
`hostname`. During compatibility, `customer == customer_id` and
`site == site_id`; conflicting values are rejected. See
[Canonical identity](canonical-identity.md).

The framework writer applies these tags; connector payloads are not trusted as
deployment identity. Vendor site and group data use `source_site_id`,
`source_site_name`, and `source_device_group`. See
[Telemetry hardening](telemetry-hardening.md).

Schema version 1 uses shared canonical measurements. Every native point carries
`deployment_id`; device telemetry also carries `collector`, `customer`, `site`,
`device_id`, `hostname`, `vendor`, `platform`, and `device_role` where the
source provides them. `collector_health` uses `collector`, `customer`, `site`,
and `diagnostic_category`.

## example-school reference collectors

| Collector | Measurements | Required fields | Normal interval |
| --- | --- | --- | --- |
| Palo Alto | `device`, `availability`, `performance`, `interface`, `firewall`, `license`, `content_package`, `collector_health` | device identity/online/uptime; performance CPU, memory and sessions; interface status/counters; firewall HA/certificate; subscription expiry; package version/age; run success/duration/points | 120 seconds; discovery 6 hours |
| PaperCut MF | `device`, `availability`, `performance`, `license`, `collector_health` | application identity/uptime; service/device availability; application, database, printer, disk and JVM metrics; licence validity/utilisation; run success/partial/duration/points | 60 seconds; discovery 6 hours |
| SNMP/Telegraf | canonical inventory plus configured Telegraf SNMP measurements when devices are discovered | canonical `device_id`, hostname/address, site and source; device-specific availability, performance and interface fields | Telegraf 30 seconds; discovery 1 hour |

Palo Alto interface counters are cumulative bytes, packets, errors and
discards. WAN fields are emitted only for explicitly configured interfaces.
PaperCut performance rows use a bounded `component` tag such as application,
database, or printers. An SNMP discovery with zero responsive devices is a
successful empty discovery, but it does not prove telemetry collection and must
not be represented as a successful SNMP measurement write.

## Current field sets

- `device`: `online`, `uptime_seconds`, model/serial/version/firmware,
  management IP, operating system, CPU count and platform family.
- `availability`: `available`, status, service, total and offline counts.
- `performance`: CPU and memory percentages, session counts/utilisation,
  database connection/latency, disk/JVM utilisation, and printer/device counts.
- `interface`: operational status, speed/duplex/logical state, cumulative RX/TX
  byte/packet/error/discard counters, and explicit WAN classification.
- `firewall`: HA mode/status, device-certificate status, platform family and
  software version.
- `license`: status/validity, expiry state/date/days, user utilisation and
  Upgrade Assurance remaining days.
- `content_package`: package name, version, release timestamp and age in days.
- `collector_health`: framework-owned runtime, execution mode, status,
  success, duration, generated/written points, API request/latency, retries,
  skip reason, bounded diagnostics, error and returned-device counts.

The detailed tag and field definitions remain under [`docs/schema/`](schema/).
Dashboard SQL may reference only these current measurements and documented
fields. Missing optional telemetry must render **Not collected**, **Not
Enabled**, or another deliberate canonical state rather than generic
**No data**.

Canonical fields are coerced and validated before line protocol is generated.
Validation failures name the measurement, field, expected/received type,
connector, and point number without exposing raw responses.

## Validation

For a live profile, use `SHOW TABLES`, `SHOW COLUMNS FROM <measurement>`, and a
collector-grouped count/latest query. Validate the source dashboard JSON and
the managed runtime copy. A measurement existing only because of old data is
not proof: baseline validation starts from an empty profile database.
