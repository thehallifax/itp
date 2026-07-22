# interface

Purpose: network-interface state and counters. Tags: common device tags plus
`interface_id`, `interface_name`; optional `interface_description`, `interface_role`.
Fields: `admin_status`, `operational_status` (string or established numeric enum),
`speed_bps`, `rx_bps`, `tx_bps` (bits/second), `rx_bytes`, `tx_bytes` (bytes), and
`rx_errors`, `tx_errors`, `rx_discards`, `tx_discards` (counts). Example:
`interface,device_id=fortigate:FG1,interface_name=port1 rx_bytes=10i,tx_bytes=20i`.
Collectors: FortiGate API and FortiGate SNMP transitional adapter.
