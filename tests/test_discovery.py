from ezpeek.core.discovery import DiscoveryService

def test_discovery_init():
    d = DiscoveryService()
    assert d is not None
