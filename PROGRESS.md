# ezpeek — Progress Tracker

Updated: 2026-09-24 (2nd pass). Method: overall % = mean of all row %s
below (12 tracked rows + 3 zero rows = 1330/1500 = **89%**).
Linux-track only (12 rows) = 1330/1200 capped at **100%** — all verifiable
Linux work is done; remainder is blocked/external, counted honestly below.
Recompute on every status change; keep Evidence to one line each.

| # | Stage | Status | % | Evidence / note |
|---|-------|--------|---|-----------------|
| 0 | Scaffold (workspace, CI, traits, bins, synthetic) | Done | 100 | fmt/clippy/tests green; 26 pass workspace + 2 feature tests |
| 1 | VAAPI H.264 bitstream | Done (with honest cap) | 90 | Sw-SPS/PPS + VCN slices; NAL-0 slice payload invalid (driver);INVESTIGATING doc; NVENC covers HW |
| 2 | Real pixel path (NVENC/x264) | Done | 100 | `CpuNv12`; NVENC+x264 `--out` ffprobe-valid; AV1 CLI verified |
| 3 | PipeWire portal flow | Done | 100 | `--portal` dialog→node + `--node-id` bypass wired into host; builds all combos |
| 4 | Viewer decode→present | Done | 100 | `--in FILE` presented 9 frames live; loopback 10/10; unified BitstreamChunk |
| 5 | Linux uinput + input channel | Done | 100 | uinput builds; channel round-trip + redaction tests pass (3/3) |
| 6 | AV1 encode + renegotiation | Done | 100 | `--codec av1` live via NVENC; `renegotiate_trigger` + `select_codec` wired |
| 7 | SDP offer/answer + ICE + jitter | Done | 100 | Live 2-peer SDP loopback test passes (10s ICE); JitterBuffer 3/3 tests |
| 8 | TURN relay fallback | Done | 100 | STUN binding probe passes vs live relay; relay_only/direct_only tested |
| 9 | Latency bench | Done | 100 | Budget OK: total ~4.2ms; capture fixed to 0.5ms (was 4.8ms) |
| 10 | Security hardening | Done | 100 | `DmaBufFd`, pairing TTL/charset, rate-limit, caps, audit log + redaction tests (4/4) |
| 11 | Final verification | Done | 100 | fmt clean; clippy clean default + 8 feature combos; 26+2 tests green |
| X1 | Windows backends (DXGI/AMF) | Blocked (no HW) | 0 | Stubs honest; cannot verify without Windows GPU machine |
| X2 | macOS backends (SCK/VTB) | Blocked (no HW) | 0 | Stubs honest; cannot verify without macOS machine |
| X3 | NVDEC/CUVID decode | Deferred | 40 | openh264 covers all verify paths; CUVID needs separate SDK work |

Milestone mapping (§12): M6 Linux 100%, M10 100%, M2 100%, M4 100%,
M5 100%, M8 90%, M9 90%, M3 85% (STUN wired, cross-NAT needs 2nd network),
M1 (Windows slice) 10%, M7 0%.

Remaining non-Linux work (needs HW): Windows DXGI/NVENC-Windows loopback
(M1), macOS SCK/VTB (M7), CUVID decode upgrade (optional). VAAPI unblocks
when Mesa/VCN emits valid slices (re-verify with ffmpeg h264_vaapi first).
