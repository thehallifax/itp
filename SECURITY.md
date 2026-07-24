# Security policy

ITP is Alpha software and should be evaluated accordingly. Do not assume the
platform or its upgrade path is production-hardened.

## Reporting a vulnerability

Do not publish sensitive exploit details in a public issue. Contact the
repository maintainer privately through an available private GitHub contact
channel and include:

- the affected component and version or commit;
- the conditions required to reproduce the issue;
- the potential impact;
- a minimal sanitized reproduction, if safe.

Do not include live credentials, customer names, addresses, telemetry, or
production evidence. The maintainer can arrange an appropriate private channel
for any additional detail.

## Secrets and customer data

Never commit:

- root or profile `.env` files;
- API tokens, passwords, SNMP communities, certificates, or private keys;
- customer identifiers, production hostnames, management addresses, or raw
  production evidence;
- generated `runtime/`, backup, or local Compose override content.

The repository tracks only safe templates such as `.env.example` and
`secrets/**/*.env.example`. Copy the required example locally, populate the
ignored `.env` counterpart, and inject it only into the service that needs it.
Docker build contexts exclude root environment and secret directories.

If a secret is committed or exposed, revoke and replace it immediately, remove
it from the affected environment, and notify the maintainer privately. Removing
the value from a later commit does not invalidate the exposed credential.
