# PaperCut MF connector

The PaperCut connector reads the PaperCut MF System Health API over HTTPS. It
collects application, database, printer, embedded-device, service, and licence
health without changing PaperCut configuration.

## Configure

Copy `secrets/papercut.env.example` to the ignored
`secrets/papercut.env`. Set `PAPERCUT_AUTHORIZATION_KEY` only when the API is
configured to require an Authorization header.

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

Use a certificate trusted by the collector container. Disabling TLS validation
is supported for controlled testing but is not recommended in production.
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
