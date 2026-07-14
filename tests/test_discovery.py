from ezpeek.core.discovery import MAGIC, BROADCAST_PORT, _get_subnet_broadcast


def test_magic_and_port():
    assert MAGIC == "EZPEEK_HELLO"
    assert BROADCAST_PORT == 27787


def test_subnet_broadcast():
    assert _get_subnet_broadcast("10.0.0.3") == "10.0.0.255"
    assert _get_subnet_broadcast("192.168.1.50") == "192.168.1.255"
    assert _get_subnet_broadcast("0.0.0.0") is None
