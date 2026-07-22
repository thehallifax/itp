import pytest
from discover import classify, enumerate_addresses


@pytest.mark.parametrize("oid,vendor,platform", [
    ("1.3.6.1.4.1.12356.1", "fortinet", "fortigate"),
    ("1.3.6.1.4.1.14823.1", "aruba", "network-switch"),
    ("1.3.6.1.4.1.1347.1", "kyocera", "printer"),
    ("1.3.6.1.4.1.318.1", "apc", "ups"),
    ("1.3.6.1.4.1.99999.1", "unknown", "unknown"),
])
def test_classification(oid, vendor, platform):
    assert classify(oid)[:2] == (vendor, platform)


def test_hp_printer_disambiguates_enterprise_11():
    assert classify("1.3.6.1.4.1.11.2.3.9", "HP LaserJet printer") == ("hp", "printer", "printer")


def test_known_juniper_ex_switch_remains_switch():
    assert classify("1.3.6.1.4.1.2636.1.1.1.2.109", "For grafana dash") == ("juniper", "network-switch", "switch")
    assert classify("1.3.6.1.4.1.2636.1.1.1.2.999", "Juniper Networks EX4300") == ("juniper", "network-switch", "switch")


def test_juniper_mist_ap():
    assert classify("1.3.6.1.4.1.2636.1.1.1.2.999", "Juniper Mist AP45") == ("juniper", "wireless-access-point", "access-point")


def test_juniper_enterprise_alone_is_unknown_even_on_wireless_network():
    expected = ("juniper", "unknown", "unknown")
    assert classify("1.3.6.1.4.1.2636.99") == expected
    assert classify("1.3.6.1.4.1.2636.99", purpose="wireless") == expected


@pytest.mark.parametrize("oid,description,vendor", [
    ("1.3.6.1.4.1.14823.1", "Aruba AP-515 access point", "aruba"),
    ("1.3.6.1.4.1.9.1", "Cisco AIR-AP3802", "cisco"),
    ("1.3.6.1.4.1.25053.1", "Ruckus ZoneFlex R710", "ruckus"),
    ("1.3.6.1.4.1.1916.1", "Extreme wireless AP410C", "extreme"),
    ("1.3.6.1.4.1.41112.1", "Ubiquiti UniFi U6 Pro", "ubiquiti"),
])
def test_wireless_vendor_classification(oid, description, vendor):
    assert classify(oid, description) == (vendor, "wireless-access-point", "access-point")


def test_wireless_purpose_only_breaks_vendor_tie():
    oid = "1.3.6.1.4.1.14823.1"
    assert classify(oid, "Aruba Networks device")[1] != "wireless-access-point"
    assert classify(oid, "Aruba Networks device", purpose="wireless") == ("aruba", "wireless-access-point", "access-point")
    assert classify("1.3.6.1.4.1.99999", "generic device", purpose="wireless") == ("unknown", "unknown", "unknown")


def config(cidr, exclusions=()):
    return {"discovery": {}, "networks": [{"cidr": cidr}], "exclusions": list(exclusions)}


def test_cidr_22_guard():
    assert len(enumerate_addresses(config("10.0.0.0/22"))) == 1022
    with pytest.raises(ValueError): enumerate_addresses(config("10.0.0.0/21"))


def test_exclusion_handling():
    assert enumerate_addresses(config("192.0.2.0/30", ["192.0.2.1"])) == ["192.0.2.2"]
