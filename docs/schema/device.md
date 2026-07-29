# device

Purpose: current device identity and general state. Tags: `collector`, `customer`, `site`,
`device_id`, `hostname`, `vendor`, `platform`, `device_role`; optional `model`. Fields:
`online` (boolean), `uptime_seconds` (seconds), `model`, `serial`, `firmware` (strings).
Example: `device,collector=mist,customer=acme,site=hq,device_id=mist:1 online=true,uptime_seconds=3600`.
Collectors: Mist, FortiGate API, Palo Alto, PaperCut MF, and the SNMP/Telegraf
transitional adapter.
