# wireless

Purpose: wireless access-point telemetry. Tags: common device tags plus optional `model`,
`radio`, `band`, `ssid`. Fields: `online` (boolean), `client_count` (count), `rx_bytes`,
`tx_bytes` (bytes), `rx_bps`, `tx_bps` (bits/second), optional channel/utilization fields.
Example: `wireless,collector=mist,device_id=mist:ap1 client_count=24i,online=true`.
Collectors: Mist and wireless SNMP transitional adapter.
