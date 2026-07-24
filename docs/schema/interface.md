# interface

Purpose: network-interface state and counters. Tags: common device tags plus
`interface_id`, `interface_name`; optional `interface_description`, `interface_role`.
Fields: `admin_status`, `operational_status` (string or established numeric enum),
`speed_bps`, `rx_bps`, `tx_bps` (bits/second), `rx_bytes`, `tx_bytes` (bytes), and
`rx_errors`, `tx_errors`, `rx_discards`, `tx_discards` (counts). Example:
`interface,device_id=fortigate:FG1,interface_name=port1 rx_bytes=10i,tx_bytes=20i`.
Collectors: FortiGate API and FortiGate SNMP transitional adapter.

Palo Alto fields include `admin_status`, `operational_status`, `speed`,
`duplex`, `logical`, `rx_bytes_total`, `tx_bytes_total`,
`rx_packets_total`, `tx_packets_total`, `rx_errors_total`,
`tx_errors_total`, and `rx_discards_total`.

Explicit operator configuration may add `wan_classified`, `wan_role`, and
`wan_display_name`. Interfaces are never classified as WAN from their number,
name, traffic volume, or route position.

Counters are unsigned cumulative observations. A decrease indicates a reset;
rate consumers must omit negative deltas.

Collectors: Palo Alto, FortiGate API, and the FortiGate SNMP transitional
adapter.
