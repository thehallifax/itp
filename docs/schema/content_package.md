# content_package

## Purpose

Installed security and operational content package currency.

## Tags

`collector`, `customer`, `site`, `device_id`, `hostname`, and bounded
`package_name`.

## Fields and units

`version`, `release_time` (UTC timestamp), `release_time_raw`, and `age_days` (days).
An invalid or unknown release time leaves `release_time` and `age_days`
absent; it never produces age zero.

## Example

`package_name="applications", version="9000-1234", age_days=2`

## Supported collectors

Palo Alto.
