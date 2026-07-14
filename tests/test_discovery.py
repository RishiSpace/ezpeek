from ezpeek.core.discovery import MAGIC, BROADCAST_PORT, _broadcast_targets


def test_magic_and_port():
    assert MAGIC == "EZPEEK_HELLO"
    assert BROADCAST_PORT == 27787


def test_broadcast_targets_include_wide_masks():
    t = _broadcast_targets("10.0.0.3")
    assert "255.255.255.255" in t
    assert "10.0.0.255" in t
    assert "10.0.255.255" in t  # /16 — critical for 10.0.0.x ↔ 10.0.7.x LANs
    t2 = _broadcast_targets("10.0.7.26")
    assert "10.0.7.255" in t2
    assert "10.0.255.255" in t2
