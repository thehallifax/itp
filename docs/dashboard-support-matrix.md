# Connector dashboard support matrix

Managed connector dashboards are validated against representative points from
their collector normalizers. `Not Yet Collected` means the query has not yet
received a successful sample. Query and datasource failures remain Grafana
errors and must never be described as healthy empty results.

## Mist Infrastructure Overview

Mist sources are `/api/v1/orgs/{org}/inventory`, `/stats/devices`, and the site
catalogue. `normalize_device`, `metric_fields`, and `metric_points` in
`collectors/mist/normalizer.py` own the projection. Current Mist points retain
the emitted compatibility identity tags `customer` and `site`; the dashboard
therefore filters `site`, not the nonexistent `site_id` column.

| Panel | Source | Measurement | Required tags | Required fields | History | Support |
|---|---|---|---|---|---|---|
| Total Devices Reporting | Inventory + device stats | `infrastructure_device` | collector, site, device_id, hostname, platform | online | Latest per device | Supported |
| Online Devices | Device stats status | `infrastructure_device` | collector, site, device_id | online | Latest per device | Supported |
| Offline / Disconnected | Device stats status | `infrastructure_device` | collector, site, device_id | online | Latest per device | Supported |
| Last Collection Successful | Framework run outcome | `collector_health` | collector | success | Latest run | Supported after first run |
| Devices by Type | Inventory type/model | `infrastructure_device` | collector, site, platform, device_id | online | Latest per device | Supported |
| Devices by Site | Site catalogue + inventory site ID | `infrastructure_device` | collector, site, device_id | online | Latest per device | Supported |
| Recently Offline Devices | Device stats status | `infrastructure_device` | collector, site, device_id, hostname, model, platform | online, uptime_seconds | Latest per device | Supported; empty means no current offline devices |
| Access Point Usage | AP device stats | `wireless_access_point` | collector, site, device_id, hostname, model | online, client_count, rx_bps, tx_bps | Latest per AP | Partially supported; client and rate fields are conditional on Mist responses |
| Device Health | Device stats | `infrastructure_device` | collector, site, device_id, hostname, model, platform | cpu_percent, memory_used_percent, uptime_seconds | Latest per device | Partially supported; resource metrics vary by device class |
| Collector Health | Framework run outcome | `collector_health` | collector | duration_ms, devices_returned, points_written, api_requests, retry_count, error_count | Last 20 runs | Supported after first run |

## PaperCut MF Operational Overview

PaperCut sources are `/api/health`, `/api/health/devices`, and
`/api/health/printers`. `normalize` in `collectors/papercut/normalizer.py` owns
all projections. The connector does not scrape the administrative UI.

| Panel | Source property | Measurement | Required tags | Required fields | History | Support |
|---|---|---|---|---|---|---|
| Overall Health | Root health and deterministic conditions | `availability` | collector, customer_id, site_id | status | Latest | Supported |
| Application Version | applicationServer.systemInfo.version | `device` | collector, customer_id, site_id | version | Latest | Supported |
| Uptime | applicationServer.systemMetrics.uptimeHours | `device` | collector, customer_id, site_id | uptime_seconds | Latest | Supported |
| Printers | printers.count | `performance` (`component=printing`) | collector, customer_id, site_id, component | printer_count | Latest | Supported |
| Embedded Devices | devices.count | `performance` (`component=printing`) | collector, customer_id, site_id, component | device_count | Latest | Supported |
| Application Resources | applicationServer.systemMetrics | `performance` (`component=application`) | collector, customer_id, site_id, component | cpu_percent, jvm_memory_used_percent, disk_used_percent | Multiple samples | Supported |
| Database Health | database | `performance` (`component=database`) | collector, customer_id, site_id, component | status, connection counts, query_latency_ms, connection_latency_ms | Latest | Supported |
| Printer and Device Summary | printers + devices | `performance` (`component=printing`) | collector, customer_id, site_id, component | printer_count, printer_errors, held_jobs, device_count, device_errors | Latest | Supported |
| Service Health | mobilityPrintServers, printProviders, siteServers, webPrint, job-ticketing | `availability` | collector, customer_id, site_id, service | status, total, offline | Latest per service | Supported |
| Licensing | license | `license` | collector, customer_id, site_id, license_type | valid, users_used, users_licensed, user_utilisation_percent, upgrade_assurance_remaining_days, installed_packs | Latest | Supported when licence properties are returned |
| Active Operational Findings | database + printing summaries | `performance` | collector, customer_id, site_id, component | status, printer_errors, device_errors, held_jobs | Latest | Supported; empty means no current findings |
| Collector Health | Framework run outcome | `collector_health` | collector, customer_id, site_id | success, duration_ms, points_written, api_requests, retry_count, error_count | Last 20 runs | Supported after first run |

## Unsupported PaperCut metrics

The documented System Health APIs do not expose toner or consumable remaining
percentages. ITP does not create a consumables panel, fabricate toner fields,
or scrape PaperCut administrative HTML. A future documented API capability can
add this only through the canonical printing contract.
