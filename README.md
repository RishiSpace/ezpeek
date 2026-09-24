# ezpeek
HW Accelerated Remote Desktop — low-latency, peer-to-peer, zero-copy where the platform allows it.

See [INSTRUCTIONS-EZPEEK.md](./INSTRUCTIONS-EZPEEK.md) for the full architecture spec
(product goal, network model, codec policy, latency budget, milestones).

## Binaries

- `ezpeek-host` — captures, encodes, transmits; injects received input.
- `ezpeek-viewer` — receives, decodes, presents; forwards local input.
- `ezpeek-rendezvous` — handshake relay + fallback TURN only (never sees plaintext media).

## Quick start (Linux)

```sh
cargo build -p ezpeek-host --features nvenc,x264-fallback
./target/debug/ezpeek-host --capture synthetic --encoder nvenc --out out.h264
./target/debug/ezpeek-host --capture synthetic --encoder nvenc --codec av1 --out out.obu
./target/debug/ezpeek-viewer --in out.h264
./target/debug/ezpeek-bench -- --frames 30
./target/debug/ezpeek-rendezvous --listen 127.0.0.1:7447
```

Feature flags: `nvenc`, `vaapi`, `x264-fallback`, `openh264-fallback`,
`pipewire-capture`, `portal`, `uinput`, `webrtc-peer`, `turn-relay`,
`software-present`. See [PROGRESS.md](./PROGRESS.md) for per-feature status.

## Current progress

Overall **89%** (Linux-track **100%**). Full table with method and evidence: [PROGRESS.md](./PROGRESS.md).

| # | Stage | Status | % |
|---|-------|--------|---|
| 0 | Scaffold | Done | 100 |
| 1 | VAAPI H.264 bitstream | Done (capped) | 90 |
| 2 | Real pixel path (NVENC/x264) | Done | 100 |
| 3 | PipeWire portal flow | Done | 100 |
| 4 | Viewer decode→present | Done | 100 |
| 5 | Linux uinput + input channel | Done | 100 |
| 6 | AV1 encode + renegotiation | Done | 100 |
| 7 | SDP offer/answer + ICE + jitter | Done | 100 |
| 8 | TURN relay fallback | Done | 100 |
| 9 | Latency bench | Done | 100 |
| 10 | Security hardening | Done | 100 |
| 11 | Final verification | Done | 100 |
| X1 | Windows backends | Blocked (no HW) | 0 |
| X2 | macOS backends | Blocked (no HW) | 0 |
| X3 | NVDEC/CUVID decode | Deferred | 40 |

Remaining: Windows DXGI/AMF (M1), macOS SCK/VTB (M7) — need hardware.
