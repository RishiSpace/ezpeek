from ezpeek.core.transport import srt_url, TransportSpec, DEFAULT_SRT_LATENCY_MS


def test_srt_url_listener():
    u = srt_url("0.0.0.0", 2734, mode="listener")
    assert u.startswith("srt://0.0.0.0:2734?")
    assert "mode=listener" in u
    assert "transtype=live" in u
    assert f"latency={DEFAULT_SRT_LATENCY_MS}" in u


def test_srt_url_caller():
    u = srt_url("10.0.0.3", 2734, mode="caller", latency_ms=120)
    assert "mode=caller" in u
    assert "10.0.0.3:2734" in u


def test_transport_spec_defaults():
    t = TransportSpec()
    assert t.host == "0.0.0.0"
    assert t.port == 2734
