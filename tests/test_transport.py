from ezpeek.core.transport import build_receiver_cmd


def test_transport_receiver_cmd_contains_srt():
    cmd = build_receiver_cmd("127.0.0.1", 17000)
    assert any(part.startswith("srt://127.0.0.1:17000") for part in cmd)
