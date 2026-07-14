from ezpeek.core.control import ControlServer, ControlClient, test_control_roundtrip


def test_control_roundtrip_local():
    ok, port = test_control_roundtrip()
    assert ok is True
    assert port > 0


def test_control_bind_all_interfaces():
    srv = ControlServer(host="0.0.0.0", port=0)
    port = srv.start()
    assert port > 0
    cli = ControlClient()
    assert cli.connect("127.0.0.1", port, retries=1)
    assert cli.send("PING")
    cli.close()
    srv.stop()
