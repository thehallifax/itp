# PaperCut MF connector

The PaperCut connector reads the PaperCut MF System Health API over HTTPS. It
collects application, database, printer, embedded-device, service, and licence
health without changing PaperCut configuration.

## Configure

Copy `secrets/papercut.env.example` to the ignored
`secrets/papercut.env`. Set `PAPERCUT_AUTHORIZATION_KEY` when the API requires
the System Health `Authorization` query parameter.

Runtime onboarding accepts a hostname with an optional port or an HTTPS origin.
It normalizes hostnames to HTTPS and removes a trailing `/api/health`; other
paths and HTTP URLs are rejected. No fixed port is required. The optional
authorization key uses hidden input.

Add the endpoint and canonical site to `discovery/config.yml`:

```yaml
collectors:
  papercut:
    enabled: true
    base_url: https://print.example.invalid:9192
    authorization_key_env: PAPERCUT_AUTHORIZATION_KEY
    verify_tls: true
    site: main-campus
```

## HTTP request contract

ITP sends an HTTP `GET` request to the application-server origin plus
`/api/health`, followed by a separate `GET /api/health/devices` request.
The System Health authorization key is encoded once as the `Authorization`
query parameter:

```text
/api/health?Authorization=<key>
```

Requests use `Accept: application/json`, have no request body or
`Content-Type`, and do not follow redirects automatically. Structured URL
construction ensures there is no trailing slash before the query string.
Configure `base_url` as the HTTPS application-server origin; trailing slashes
or a trailing `/api/health` are normalized to the same origin.

The value in `PAPERCUT_AUTHORIZATION_KEY` must be the System Health interface
authorization key from PaperCut's **Options > Advanced > System Health
Monitoring** section. Surrounding spaces, tabs, CR, and LF are removed;
embedded control characters are rejected.

For HTTP failures, `collector test papercut --json` reports the status, method,
sanitized path, response content type, and a bounded response excerpt.
Authorization values, cookies, headers, and unbounded HTML are never returned.
An HTTP 400 is classified as `invalid_request`; use its sanitized response
excerpt to distinguish an invalid key or server-side System Health
configuration from a malformed endpoint.

`verify_tls` defaults to `true`. In this mode the connector uses normal public
trust plus any deployment CA bundle installed with `itp credentials ca`.
Setting `verify_tls: false` disables certificate verification only for
PaperCut. It is intended only for trusted internal networks and creates a
traffic-interception and server-impersonation risk. Doctor, status, collector
test, and the collector log report the disabled policy explicitly. It does not
change TLS verification for any other HTTPS connector.
The connector reads `/api/health` and `/api/health/devices`; a failure of the
optional device-detail request is reported as a partial collection.

Thresholds for disk, JVM memory, held jobs, Upgrade Assurance, and long uptime
are configurable in `discovery/config.example.yml`. Operational findings are
deterministic and vendor-neutral downstream of collection.

PaperCut documents API enablement and authorization in its
[configuration guide](https://www.papercut.com/help/manuals/ng-mf/common/tools-monitor-system-health-api-configure/)
and the available fields in the
[System Health API reference](https://www.papercut.com/help/manuals/ng-mf/common/tools-monitor-system-health-api-reference-system-info/).

Authentication, TLS, timeout, connectivity, malformed-response, and partial
failures are categorized without logging the endpoint key.

PaperCut populations are intentionally distinct: `printer_count` is the
configured printer-object aggregate reported by System Health; embedded-device
count is the number of detailed device records; canonical operational printer
assets are only individually identified embedded devices admitted by
normalisation. These values are labelled separately and are not forced to
match.

```sh
./itp collector test papercut --deployment <deployment>
./itp collector run papercut --deployment <deployment>
./itp status --deployment <deployment>
./itp dashboard generate --deployment <deployment>
./itp logs collector --deployment <deployment>
```
