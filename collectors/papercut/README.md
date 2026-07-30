# PaperCut MF connector

The PaperCut connector reads the PaperCut MF System Health API over HTTPS. It
collects application, database, printer, embedded-device, service, and licence
health without changing PaperCut configuration.

## Configure

Copy `secrets/papercut.env.example` to the ignored
`secrets/papercut.env`. Set `PAPERCUT_AUTHORIZATION_KEY` only when the API is
configured to require an Authorization header.

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

`verify_tls` defaults to `true`. In this mode the connector uses normal public
trust plus any deployment CA bundle installed with `itp credentials ca`.
Setting `verify_tls: false` disables certificate verification only for
PaperCut. It is intended only for trusted internal networks and creates a
traffic-interception and server-impersonation risk. Doctor, status, collector
test, and the collector log report the disabled policy explicitly. It does not
change TLS verification for any other HTTPS connector.
The connector reads `/api/health/` and `/api/health/devices`; a failure of the
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
