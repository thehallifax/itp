# inventory

Purpose: durable asset identity rather than time-series telemetry. Tags are not used
because this is a persisted inventory contract. Required fields/properties:
`asset_id`, `collector`, `customer`, `site`, `hostname`, `vendor`, `platform`,
`device_type`, `device_role`, `lifecycle_state`; optional `serial_number`, `mac_address`,
`management_ip`, `model`, `firmware_version`. Units: times are RFC3339 UTC. Example asset ID:
`fortigate:FGT60F123456`. Collectors: all through `InventoryEngine`; this is persisted in
`runtime/inventory`, not an Influx measurement in schema version 1.
