# license

## Purpose

Subscription and entitlement state reported by infrastructure collectors.

## Tags

`collector`, `customer`, `site`, `device_id`, `hostname`,
`subscription_name`.

## Fields and units

`status`, `expiry_date`, `remaining_days` (days), `expired_days` (days), `expired`,
`perpetual`, `expiry_state`, `raw_status`, and `raw_expiry`.

`remaining_days` is never negative. An expired subscription has
`expired=true`, `remaining_days=0`, and a non-negative `expired_days`.
`expiry_state` distinguishes `active`, `expired`, `perpetual`, `unavailable`,
and `malformed`.

## Example

`subscription_name="Threat Prevention", expiry_state="active", remaining_days=28`

## Supported collectors

Palo Alto and PaperCut MF. PaperCut uses `license_type` for installed packs and
adds licence validity, user utilisation, and Upgrade Assurance fields.
