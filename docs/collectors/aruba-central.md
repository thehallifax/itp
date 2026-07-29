# HPE Aruba Networking Central

The Aruba Central collector is a read-only, central-runtime connector. It
discovers Central groups, sites and device inventory, then emits canonical
`device`, `availability`, `wireless` and `collector_health` measurements.

## Configuration

Copy `secrets/aruba.env.example` to the ignored root or profile secret
namespace and configure the collector:

```yaml
collectors:
  aruba:
    enabled: true
    execution: central
    base_url: https://api-ap.central.arubanetworks.com
    token_url: https://api-ap.central.arubanetworks.com/oauth2/token
    auth_mode: refresh_token
    account_id: workspace-or-account-id
    site: site:reference
    client_id_env: ARUBA_CENTRAL_CLIENT_ID
    client_secret_env: ARUBA_CENTRAL_CLIENT_SECRET
```

Use the regional URL assigned to the Central account. The initial endpoint
contract targets Classic Central and uses `auth_mode: refresh_token`; rotated
access and refresh tokens remain process-local and are never written to runtime
output. Because rotated refresh tokens are not persisted, restarting the
collector may require the originally configured refresh token to remain valid.
ITP deliberately does not persist rotated credentials until a secure
credential-storage contract is available. New Central client credentials are supported with
`auth_mode: client_credentials` and the HPE SSO token URL. Endpoint paths remain
configurable for regional/API-version compatibility.

## Discovery and identity

The connector records the canonical `deployment_id`, `customer_id` and
`site_id` from the active profile. Aruba group and site names are descriptive
source metadata, never canonical joins. Devices use `aruba:<serial>` as the
stable `device_id`; hostname or display-name changes do not create a new asset.

Discovery reports account identity, group/site counts and available device
inventory. An empty inventory is a deliberate `no_devices` state, not a
transport failure.

Access points, switches and gateways are collected as independent resource
classes. A successful endpoint returning zero resources is a valid collected
capability with `resource_count: 0`. Switch, gateway and alert endpoints are
additive: an unsupported or inaccessible optional endpoint is reported in that
capability without discarding AP inventory or degrading an otherwise successful
AP-only collection. Failure of the AP endpoint does fail the collection because
wireless AP inventory is the connector's initial authoritative resource.

## Safe live diagnostics

With the profile configuration and ignored Aruba secret file in place, run:

```bash
python -m collectors --profile example-school inspect aruba
python -m collectors --profile example-school inspect aruba --json
python -m collectors --profile example-school capabilities inspect --json
```

The live inspection authenticates and reads discovery endpoints without writing
telemetry. It displays the resolved API base URL, authentication and account
discovery results, group/site/AP/switch/gateway counts, endpoint capability
states and readiness. Output excludes client secrets, bearer and refresh
tokens, authorization headers and raw API responses.

## Readiness categories

- `invalid_credentials`: OAuth credentials were rejected.
- `token_expired`: an access token could not be renewed.
- `api_unavailable`: transport, rate-limit or service failure.
- `insufficient_permissions`: the API role denied an endpoint.
- `unsupported_endpoint`: the selected Central variant lacks an endpoint.
- `no_devices`: authentication and discovery succeeded but returned no devices.

Inspect current support and runtime evidence with:

```bash
python -m collectors --profile PROFILE capabilities inspect --json
python -m collectors --profile PROFILE inspect aruba
```

The initial collector has no dashboard and creates no Operations rules.

## Collector boundary

Ruckus access points are outside the Aruba Central collector boundary. A future
Ruckus collector will project its devices into the same canonical AP inventory
and telemetry model. Cross-vendor operational dashboards must consume canonical
wireless telemetry rather than Aruba-specific response structures.
