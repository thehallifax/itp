# Security and secrets

Secrets belong only in:

```text
runtime/deployments/<deployment-id>/secrets/
runtime/deployments/<deployment-id>/generated/deployment.env
```

The CLI uses hidden input for secret connector fields and applies owner-only
permissions on macOS and Linux. It never echoes entered values. Generated
service passwords and tokens are preserved during idempotent redeployment.

Tracked `*.env.example` files contain empty placeholders only. Never copy a
runtime directory into source, attach it to an issue, or include it in support
evidence. Rotate any credential that may have appeared in prior public history;
creating a sanitised repository does not revoke an exposed credential.

Bind services to localhost unless remote access is required. Protect remote
bindings with host firewall rules and a trusted management network.
