import re

from app.hypervisors.defaults import generate_mac_address

_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def test_esxi_mac_stays_within_vmwares_manual_range():
    """VMware reserves 00:50:56:00:00:00-00:50:56:3F:FF:FF for
    administrator-assigned addresses specifically so these can never
    collide with one ESXi generates itself (00:50:56:40:00:00 and up).
    Straying outside that range would risk exactly the collision this is
    meant to avoid."""
    for _ in range(200):
        mac = generate_mac_address("esxi")
        assert _MAC_RE.match(mac), mac
        oui, fourth, *_ = mac.split(":")
        assert oui == "00:50:56"
        assert int(fourth, 16) < 0x40


def test_generated_macs_are_not_all_identical():
    """Sanity check against an accidentally-constant generator: 50 draws
    from a large enough random space shouldn't collide down to one value."""
    macs = {generate_mac_address("esxi") for _ in range(50)}
    assert len(macs) > 1
