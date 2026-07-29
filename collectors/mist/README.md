# Juniper Mist collector

This read-only native collector uses an organization API token and polls:

- `GET /api/v1/orgs/{org_id}/sites`
- `GET /api/v1/orgs/{org_id}/inventory`
- `GET /api/v1/orgs/{org_id}/stats/devices`

The token needs organization-wide read access to sites, inventory, and device
statistics. Create an organization token with organization scope and the `read`
role. Configure the regional Mist API URL when the tenant is not hosted at
`https://api.mist.com`.
The API hostname follows the tenant region; for example,
`manage.ac2.mist.com` maps to `api.ac2.mist.com`.
Enter a complete HTTPS origin with no `/api/v1` path. Runtime onboarding
defaults to `https://api.mist.com`; an Australian-region example is
`https://api.ac2.mist.com`. Organisation IDs are UUID-like values.
`MIST_API_TOKEN` is always requested using hidden input.

Create the local secret file without putting vendor credentials in the root
`.env`:

```sh
cp secrets/mist.env.example secrets/mist.env
chmod 600 secrets/mist.env
```

Populate `MIST_ORG_ID` and `MIST_API_TOKEN` in that file. Docker Compose injects
it only into the `collector` service. Both `secrets/*.env` and the entire
`secrets/` Docker build context are excluded; only `.env.example` files may be
committed.

The YAML values remain `${MIST_ORG_ID}` and `${MIST_API_TOKEN}`.
`collectors/config.py` resolves whole-value `${NAME}` placeholders from the
collector process environment. Missing values become empty strings, causing an
actionable startup error that names the required variables but never their
values.

Default discovery and collection intervals are six hours and two minutes.
Pagination uses `limit`/`page` and validates `X-Page-Total`, with a 100-page
safety limit. HTTP 429 and transient 5xx responses receive bounded retries.

Inventory entries use `mist:` identities and remain separate from SNMP records.
AP, switch, Mist Edge, and WAN Edge types are normalized. Missing devices remain
as `stale` for seven days, while Mist's connected state is retained separately as
`operational_status`.

Device statistics map to `infrastructure_device`; APs also produce
`wireless_access_point`. `collector_health` reports polling and write health.
Missing API values are omitted rather than emitted as zero.

Day-one commands:

```sh
docker compose run --rm collector python -m collectors discover mist
docker compose run --rm collector python -m collectors collect mist
docker compose run --rm collector python -m collectors inspect mist
docker compose up -d influxdb3-core collector grafana
docker compose logs --tail=200 collector
```

The inspection command reports sites, devices, normalized telemetry coverage,
tag cardinality, and Influx write success without printing tokens or raw API
responses. Grafana provisions **Mist Infrastructure Overview** in the
Infrastructure folder. HTTP 401 means invalid credentials, 403 means the token
lacks organization read access, and 429 means the tenant is rate-limiting calls.

Current exclusions include clients, SLEs, Marvis, alarms, configuration, switch
ports, radio/SSID details, webhooks, and all API write operations.
