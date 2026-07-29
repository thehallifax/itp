# Collector onboarding

The connector registry is the authoritative catalogue for configuration,
credential fields, validation support, dashboards, implementation, and
operator guidance.

```bash
./itp collector list
./itp collector add <collector>
./itp collector test <collector>
./itp collector remove <collector>
./itp dashboard generate
```

Add writes only to the selected runtime deployment. Secret fields use hidden
input and may be left blank for later completion. Remove disables collection;
it does not delete historical telemetry or user-created dashboards.

Connection tests execute the registered read-only collector inspection and
return redacted output. Consult the collector-specific document when an
endpoint, role, certificate, or regional API setting is required.
