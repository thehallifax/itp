import json
from datetime import datetime, timedelta, timezone
from discover import generate_configs, group_devices, merge_inventory, toml_string


CFG = {"customer": "cust", "site": "site"}


def device(ip="192.0.2.1", platform="printer", vendor="kyocera", index=0):
    return {"ip": ip, "hostname": "p", "description": "d", "sys_object_id": "1.3.6.1.4.1.1347.1",
            "location": "x", "vendor": vendor, "platform": platform,
            "device_role": "printer" if platform == "printer" else "switch",
            "snmp_version": 2, "community_index": index, "first_seen": "2026-01-01T00:00:00Z",
            "last_seen": "2026-01-02T00:00:00Z", "status": "active"}


def test_first_seen_and_no_community_in_inventory():
    old = {"devices": [device()]}
    inv = merge_inventory(CFG, [("192.0.2.1", 0, ["d2", "1.3.6.1.4.1.1347.1", "p2", "x2"])], old, "2026-01-03T00:00:00Z")
    assert inv["devices"][0]["first_seen"] == "2026-01-01T00:00:00Z"
    assert "public" not in json.dumps(inv) and "community" not in inv["devices"][0]


def test_unreachable_retention():
    now = datetime.now(timezone.utc)
    d = device(); d["last_seen"] = (now - timedelta(days=6)).isoformat()
    inv = merge_inventory(CFG, [], {"devices": [d]}, now.isoformat())
    assert inv["devices"][0]["status"] == "unreachable"
    d["last_seen"] = (now - timedelta(days=8)).isoformat()
    assert merge_inventory(CFG, [], {"devices": [d]}, now.isoformat())["devices"] == []


def test_toml_escaping():
    assert toml_string('a"b\\c') == '"a\\"b\\\\c"'


def test_grouping_and_generated_agents(tmp_path):
    inv = {**CFG, "devices": [device("192.0.2.2"), device("192.0.2.1"),
                               device("192.0.2.3", "network-switch", "aruba")]}
    assert len(group_devices(inv)) == 2
    generate_configs(inv, ["secret"], tmp_path)
    printer = (tmp_path / "discovered-printers.conf").read_text()
    switch = (tmp_path / "discovered-switches.conf").read_text()
    assert '"udp://192.0.2.1:161"' in printer and '"udp://192.0.2.2:161"' in printer
    assert 'agent_host_tag = "device_ip"' in printer
    assert 'metric.tags["device_id"] = "snmp:" + address' in printer
    assert 'name = "printer_supplies"' in printer
    assert 'name = "network_interfaces"' in switch


def access_point(ip, vendor="juniper", index=0, status="active"):
    d = device(ip, "wireless-access-point", vendor, index)
    d["device_role"] = "access-point"
    d["status"] = status
    return d


def test_access_points_group_separately_and_never_as_switches(tmp_path):
    inv = {**CFG, "devices": [access_point("192.0.2.11"), access_point("192.0.2.12"),
                               access_point("192.0.2.13", "aruba", 1),
                               device("192.0.2.20", "network-switch", "juniper")]}
    groups = group_devices(inv)
    ap_groups = [key for key in groups if key[3] == "wireless-access-point"]
    assert len(ap_groups) == 2
    generate_configs(inv, ["one", "two"], tmp_path)
    aps = (tmp_path / "discovered-access-points.conf").read_text()
    switches = (tmp_path / "discovered-switches.conf").read_text()
    assert '"udp://192.0.2.11:161"' in aps and '"udp://192.0.2.12:161"' in aps
    assert '"udp://192.0.2.13:161"' in aps
    assert 'agent_host_tag = "device_ip"' in aps
    assert 'name = "wireless_interfaces"' in aps
    assert "192.0.2.11" not in switches and "192.0.2.20" in switches


def test_inactive_access_points_excluded_and_stale_file_removed(tmp_path):
    stale = tmp_path / "discovered-access-points.conf"
    stale.write_text("stale target")
    inv = {**CFG, "devices": [access_point("192.0.2.11", status="unreachable")]}
    generate_configs(inv, ["one"], tmp_path)
    assert not stale.exists()


def test_mist_observations_never_generate_telegraf_targets(tmp_path):
    mist = access_point("192.0.2.50")
    mist.update({"id": "mist:device", "source": "mist", "collector": "mist"})
    generate_configs({**CFG, "devices": [mist]}, ["one"], tmp_path)
    assert not (tmp_path / "discovered-access-points.conf").exists()
