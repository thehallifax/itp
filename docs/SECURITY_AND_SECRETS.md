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

## TLS trust

Collector and discovery images initialize Debian's maintained public CA store.
Connector SSL contexts begin with that public trust and then load any
deployment-specific CA bundle, so adding an internal CA does not remove public
roots. Import only a genuinely private issuing CA:

```sh
./itp credentials ca add <certificate.pem> --deployment <deployment>
```

Publicly issued services must present their complete server and intermediate
chain. Do not copy public roots or host-specific certificates into deployments.
Never include private keys in a CA input file. TLS verification remains enabled
by default; disabling it is suitable only for bounded diagnosis because it
permits endpoint impersonation.

## Support bundles

`./itp support bundle` excludes secret environment files, credential files,
private keys and telemetry databases. It structurally redacts sensitive keys,
known live secret values, authorization headers, URL user information and
credential query parameters, then scans every archived member before
publication. Standard bundles can retain infrastructure metadata needed for
diagnosis; `--privacy high` applies stable pseudonyms to common identities.
Always review a bundle before sharing it outside the organisation.
