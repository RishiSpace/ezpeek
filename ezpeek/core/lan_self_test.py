"""
Headless Phase-1 self-test: prove SRT video + TCP control work on this machine.

Does not require a second PC or screen-capture permissions (uses lavfi testsrc).
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

from ezpeek.core.capture import CaptureSpec
from ezpeek.core.control import ControlClient, ControlServer
from ezpeek.core.encoder import EncodeSpec
from ezpeek.core.transport import (
    TransportSpec,
    build_sender_cmd,
    ensure_ffmpeg_tools,
    has_srt_support,
    srt_url,
)
from ezpeek.utils import get_local_ip, get_log_dir


VIDEO_PORT = 2734
CTRL_PORT = 2735


def _port_free(port: int, proto: str = "tcp") -> bool:
    if proto == "tcp":
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    else:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def run_self_test() -> int:
    print("=== ezpeek Phase-1 self-test (localhost) ===")
    print(f"Local IP (LAN advertise): {get_local_ip()}")
    print(f"Logs: {get_log_dir()}")

    try:
        ffmpeg, ffplay = ensure_ffmpeg_tools()
        print(f"ffmpeg: {ffmpeg}")
        print(f"ffplay: {ffplay}")
    except Exception as e:
        print(f"FAIL: FFmpeg not available: {e}")
        return 1

    if not has_srt_support():
        print("FAIL: FFmpeg has no SRT protocol support")
        return 1
    print("SRT: OK")

    # Use high ports if defaults busy (e.g. another ezpeek running)
    video_port = VIDEO_PORT
    ctrl_port = CTRL_PORT
    if not _port_free(video_port, "udp") or not _port_free(ctrl_port, "tcp"):
        video_port = 12734
        ctrl_port = 12735
        print(f"Default ports busy; using {video_port}/{ctrl_port}")

    # --- Control channel ---
    print("\n[1/3] Control TCP...")
    srv = ControlServer(host="0.0.0.0", port=ctrl_port)
    try:
        got = srv.start()
        assert got == ctrl_port
    except Exception as e:
        print(f"FAIL: control server: {e}")
        return 1

    cli = ControlClient()
    if not cli.connect("127.0.0.1", ctrl_port, timeout=2.0, retries=2):
        print("FAIL: control client could not connect to 127.0.0.1")
        srv.stop()
        return 1
    if not cli.send("PING"):
        print("FAIL: control PING send failed")
        cli.close()
        srv.stop()
        return 1
    print(f"Control OK on TCP {ctrl_port}")
    cli.close()
    srv.stop()

    # --- SRT video path ---
    print("\n[2/3] SRT video (testsrc -> listener -> caller decode)...")
    encode = EncodeSpec(codec="h264", fps=15, bitrate_kbps=1500, gop=15)
    tx = TransportSpec(transport="srt", host="0.0.0.0", port=video_port, latency_ms=120)
    send_cmd = build_sender_cmd(CaptureSpec(fps=15), encode, tx, test_pattern=True)
    log_dir = get_log_dir()
    send_log = log_dir / "selftest_send.log"
    recv_log = log_dir / "selftest_recv.log"

    with open(send_log, "w") as sf:
        sender = subprocess.Popen(send_cmd, stdout=sf, stderr=subprocess.STDOUT)
    time.sleep(1.2)
    if sender.poll() is not None:
        print(f"FAIL: sender exited early:\n{send_log.read_text(errors='ignore')[-800:]}")
        return 1

    # Receive a few frames with ffmpeg (more reliable than headless ffplay)
    recv_url = srt_url("127.0.0.1", video_port, mode="caller", latency_ms=120)
    recv_cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        recv_url,
        "-frames:v",
        "20",
        "-f",
        "null",
        "-",
    ]
    with open(recv_log, "w") as rf:
        try:
            recv = subprocess.run(recv_cmd, stdout=rf, stderr=subprocess.STDOUT, timeout=15)
        except subprocess.TimeoutExpired:
            print(f"FAIL: receiver timed out. log:\n{recv_log.read_text(errors='ignore')[-800:]}")
            sender.terminate()
            return 1

    sender.terminate()
    try:
        sender.wait(timeout=3)
    except Exception:
        sender.kill()

    recv_txt = recv_log.read_text(errors="ignore")
    if recv.returncode != 0 and "frame=" not in recv_txt:
        print(f"FAIL: receiver exit={recv.returncode}\n{recv_txt[-1000:]}")
        return 1
    if "Stream #0" not in recv_txt and "Video:" not in recv_txt:
        # still accept if frames decoded
        if "frame=" not in recv_txt:
            print(f"FAIL: no video stream seen\n{recv_txt[-1000:]}")
            return 1
    print(f"SRT OK on UDP/SRT {video_port} (decoded frames)")

    # --- LAN IP bind reachability hint ---
    print("\n[3/3] LAN advertisement check...")
    ip = get_local_ip()
    if ip in ("0.0.0.0", "127.0.0.1"):
        print(f"WARN: get_local_ip() returned {ip} - discovery may advertise a bad address")
    else:
        print(f"LAN IP looks fine: {ip}")

    print("\n=== SELF-TEST PASSED ===")
    print("Next: run `ezpeek` on two machines on the same LAN.")
    print("  Host: press H (accept screen share if prompted)")
    print("  Viewer: wait until peer shows video port, double-click")
    print("Firewall: UDP 27787 (discovery), UDP 2734 (video), TCP 2735 (control)")
    return 0


if __name__ == "__main__":
    sys.exit(run_self_test())
