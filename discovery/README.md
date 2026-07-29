# SNMP discovery service

This Python 3.12 service scans only configured IPv4 CIDRs, performs four SNMP GETs,
maintains a seven-day inventory, and atomically generates Telegraf SNMP inputs.
It never restarts Docker. Telegraf reloads changed files using polling.

Copy `config.example.yml` to `config.yml`, set customer/site, communities in trial
order, approved networks, and exclusions. `config.yml` is ignored by Git. Networks
broader than `/22` are rejected unless `discovery.allow_large_networks: true` is
explicitly set. Use the narrowest subnets possible.

Run once from the repository root:

```sh
docker compose run --rm discovery python /app/discover.py once --config /app/config.yml
```

Run continuously with `docker compose up -d --build discovery telegraf`. Inventory
is at `runtime/inventory/devices.json`; generated files are under
`telegraf/telegraf.d/generated`. These files are service-owned and must not be
edited. Manual FortiGate collectors remain under `inputs/`. The old generic printer
and switch collectors use `.manual` suffixes as reference and are not loaded.

## Wireless access points

Supported AP vendor families are Juniper/Mist, Aruba/HPE Aruba, Cisco, Ruckus,
Extreme, and Ubiquiti. Add `purpose: wireless` to an approved network as a weak
classification hint:

```yaml
networks:
  - cidr: 192.0.2.0/24
    purpose: wireless
```

Purpose never overrides definitive device evidence. In particular, an unknown
Juniper enterprise OID remains `juniper/unknown/unknown`; known EX descriptions
remain switches. Access points are generated into
`telegraf/telegraf.d/generated/discovered-access-points.conf` and expose the
`wireless_access_point` system measurement and `wireless_interfaces` IF-MIB
measurement. Radio, client, SSID, channel, and RF metrics are not yet collected.

Inspect active APs without exposing communities:

```sh
python -c 'import json; d=json.load(open("runtime/inventory/devices.json")); print(json.dumps([x for x in d["devices"] if x["platform"] == "wireless-access-point"], indent=2))'
```

To add a model, first record its `sysObjectID` and `sysDescr`, add a narrow marker
to `AP_MODEL_MARKERS` (and its enterprise-to-vendor entry to
`WIRELESS_ENTERPRISES` when needed), then add a classifier test. Avoid broad
enterprise-only mappings because vendors often share one tree across product
families.

For SNMP failures, verify UDP/161 routing, ACLs, credentials and that devices permit
GET requests from the Docker host. Put a replacement community first, deploy it to
devices, confirm discovery, then remove the old value. Communities are used only in
generated Telegraf files and never written to inventory.

Disable automatic discovery with `docker compose stop discovery`; optionally move
generated `.conf` files out of the generated directory if polling must also stop.
