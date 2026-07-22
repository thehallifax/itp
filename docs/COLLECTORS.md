# Collectors

| Collector | Placement | Credentials | Canonical output |
| --- | --- | --- | --- |
| Mist | central | `secrets/mist.env` | device, availability, performance, wireless, collector_health |
| FortiGate API | edge | `secrets/fortigate.env` | device, availability, performance, interface, firewall, collector_health |
| SNMP/Telegraf | edge | local configuration | device, availability, performance/interface/wireless where applicable |

Collectors are enabled only in `discovery/config.yml`. `ITP_RUNTIME_MODE` defaults to
`central`. Use `python -m collectors list`, `inspect NAME`, and `collect NAME` for
registration, safe inspection, and one-shot collection.
