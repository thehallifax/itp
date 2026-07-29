# Runtime data

This directory is ignored except for this document. `./itp deploy` creates:

```text
runtime/deployments/<deployment-id>/
  deployment.yml
  collectors.yml
  dashboards.yml
  secrets/
  generated/
  logs/
  evidence/
  state/
runtime/shared/
runtime/backups/
```

Runtime content is mutable, deployment-specific, and must never be committed.
