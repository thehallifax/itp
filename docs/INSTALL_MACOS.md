# Install on macOS

Install Docker Desktop and Python 3.9 or later. Confirm Docker Desktop is
running. Git, Docker Compose v2, 8 GiB memory and 10 GiB free disk are
recommended; memory and disk shortfalls warn rather than block. Then run:

```bash
git clone https://github.com/<organisation>/<repository> infrastructure-telemetry-platform
cd infrastructure-telemetry-platform
./itp deploy
```

Accept the detected timezone or enter an IANA timezone. The default listening
address is `127.0.0.1`; choose a LAN address only when remote access is required
and protected by the host firewall.

Before writing deployment state, ITP reports each prerequisite as `PASS`,
`WARNING`, or `FAIL`. A stopped Docker Desktop daemon, missing Git, unusable
Python environment, inaccessible runtime directory, or occupied configured
port stops deployment with remediation. Start Docker Desktop or correct the
reported item and rerun `./itp deploy`.

After deployment:

```bash
./itp doctor
./itp status
./itp collector list
```

Reconfigure or troubleshoot without manually editing generated files:

```bash
./itp deployment edit --deployment <deployment>
./itp collector setup paloalto --deployment <deployment>
./itp support bundle --deployment <deployment>
./itp recover --deployment <deployment>
```

Docker images must support the Mac architecture. Doctor reports an actionable
failure when Docker or the daemon is unavailable.
