# Install on Linux

Install Python 3.9 or later, Docker Engine, and the Docker Compose v2 plugin.
Allow the operator account to use Docker, then:

```bash
git clone https://github.com/<organisation>/<repository> infrastructure-telemetry-platform
cd infrastructure-telemetry-platform
./itp deploy
```

The default service binding is localhost. Use a specific management address
only with appropriate firewall and access controls.

Validate with:

```bash
./itp doctor
./itp status
docker compose version
```
