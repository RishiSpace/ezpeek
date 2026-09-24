# AGENTS.md — ezpeek

This file instructs any AI coding agent (or human contributor) working in this
repository. Read it in full before writing code. It defines the product goal,
the required tech stack, the architecture and module boundaries, the
zero-copy data path and the peer-to-peer network model that the whole design
exists to protect, and the coding conventions that keep the hot path fast.
When a request conflicts with this document, this document wins unless the
maintainer explicitly overrides it in the conversation.

## 1. Project Overview

**ezpeek** is a low-latency remote desktop system. Its entire reason to
exist is glass-to-glass latency competitive with local play/interaction —
not "screen sharing," which tolerates 150–300ms. That means:

- Capture the framebuffer **on the GPU**, not via a CPU-side screenshot API.
- Encode with **hardware video encoders** (NVENC / AMF / Quick Sync /
  VideoToolbox), never a software x264/libaom fallback except as a
  last-resort compatibility path.
- Support **H.264** (universal compatibility, lowest encode latency, best
  low-end-hardware coverage) and **AV1** (better quality-per-bit at high
  resolution/framerate, use when both ends negotiate hardware AV1 support).
- Decode with hardware decoders and present the decoded frame **without a
  CPU round trip** wherever the platform allows it.
- Connect **peer-to-peer, directly**, whenever the network allows it. There
  is no "backend" that mediates the video stream in the normal case — media
  flows host-to-viewer over a directly established connection. Infrastructure
  beyond the two peers exists only to do what two peers behind NAT cannot do
  for themselves: find each other (rendezvous/handshake) and, when a direct
  path truly cannot be established (symmetric NAT, CGNAT on one or both
  sides), relay encrypted packets. See §3.

Three binaries come out of this repo — two peers plus one minimal, optional
infra component:

- `ezpeek-host` — runs on the machine being remoted into. Captures, encodes,
  transmits, and injects received input events. This is a *peer*, not a
  server, in the networking sense.
- `ezpeek-viewer` — runs on the viewing machine. Receives, decodes, presents,
  and captures local input to forward. Also a peer.
- `ezpeek-rendezvous` — the only actual "server" in the system. A small,
  stateless-per-session service that does two things and nothing else:
  (1) initial handshake — relaying SDP offer/answer and ICE candidates so
  the two peers can find each other, and (2) acting as a TURN relay of last
  resort when a direct path can't be established. It never has access to
  decrypted media — see §3 and §10.

### Non-goals (do not build these unless explicitly asked)
- Routing media through `ezpeek-rendezvous` by default — that's the fallback
  path, not the design center. If a PR makes the relay path the common case
  instead of the exception, that's a regression against this architecture.
- File transfer, clipboard sync, audio — may come later, not part of the
  core pipeline and must not compromise it.
- Multi-monitor mosaic capture in v1 — single display first.
- Mobile clients in v1.
- A general "screen recording" feature — this is an interactive protocol,
  not a recorder, and should not carry that complexity (b-frames, long GOPs,
  container muxing) into the encoder config.

## 2. Target Platforms

| Platform | Capture API | HW Encode | HW Decode | Present |
|---|---|---|---|---|
| Windows 10/11 | DXGI Desktop Duplication API (`IDXGIOutputDuplication`) | NVENC / AMD AMF / Intel Quick Sync (via MFT) | NVDEC / AMF / QSV (DXVA2/D3D11VA) | DXGI swap chain |
| Linux (Wayland) | PipeWire screencast portal (`xdg-desktop-portal`) → DMA-BUF | NVENC (via CUDA/EGL interop) / VAAPI (AMD, Intel) | VAAPI / NVDEC | EGL/Vulkan surface (DRM/KMS) |
| Linux (X11) | XSHM as last resort only; prefer forcing Wayland/PipeWire path | same as above | same as above | same as above |
| macOS 13+ | ScreenCaptureKit (`SCStream`, IOSurface-backed) | VideoToolbox (`VTCompressionSession`) | VideoToolbox (`VTDecompressionSession`) | `CAMetalLayer` |

Notes for the agent:
- **NVFBC** (NVIDIA's older frame-buffer capture API) is explicitly out of
  scope: it is licensed for Quadro/RTX-workstation-class GPUs only on most
  driver branches and is not reliably available on consumer GeForce cards.
  Do not build a capture backend around it without confirming licensing with
  the maintainer first.
- AV1 hardware encode is only present on: NVIDIA Ada Lovelace (RTX 40-series)
  and newer, AMD RDNA3 and newer, Intel Arc/Xe and newer. AV1 hardware decode
  has broader support (NVIDIA Ampere+, AMD RDNA2+, Apple Silicon M3+/A17+).
  **Codec availability must be runtime-detected per device, never assumed.**

## 3. Network Architecture: Peer-to-Peer by Default

This is the load-bearing architectural decision for the project — read this
section as carefully as the zero-copy requirement in §4.

### 3.1 Design

Every session is between exactly two peers: `ezpeek-host` and
`ezpeek-viewer`. The connection between them is a standard ICE-negotiated
WebRTC peer connection. `ezpeek-rendezvous` is consulted only at connection
*setup* time and, conditionally, as a relay — it is never assumed to be in
the steady-state media path.

Connection establishment order (this is what WebRTC's ICE agent already does
— don't reimplement it, just make sure the ICE server list is populated
correctly, see §5):

1. **Handshake via rendezvous.** Both peers open a short-lived WebSocket
   connection to `ezpeek-rendezvous` to exchange SDP offer/answer and ICE
   candidates. This requires the rendezvous host to be reachable by both
   peers (a public IP or DNS name), but carries no media — just session
   descriptions and candidate lists, plus pairing/auth (§10).
2. **Direct connection attempt (host/srflx candidates).** ICE tries local
   network paths first, then paths discovered via STUN (server-reflexive
   candidates) — the STUN server can be public (e.g. a well-known STUN
   service) or self-hosted alongside `ezpeek-rendezvous`. Most home/office
   NAT (full-cone, restricted-cone, port-restricted) resolves here via
   standard hole-punching. **This is the expected outcome for the large
   majority of sessions** and is the case the latency budget in §8 is
   written against.
3. **Relay via TURN (fallback only).** If ICE connectivity checks fail on
   every direct candidate pair — which happens under symmetric NAT or
   CGNAT on one or both peers, common on mobile carriers and some consumer
   ISPs — ICE falls back to a relay candidate through a TURN server.
   `ezpeek-rendezvous` bundles a TURN-compatible relay (coturn or an
   embedded equivalent) for exactly this case. Once a relay candidate pair
   is selected, media flows host → rendezvous → viewer, but still as opaque
   encrypted SRTP — the rendezvous process cannot decrypt it (§10).

The rendezvous role after step 1 is done for a WebSocket-only handshake; if
step 3 is needed, the *same* `ezpeek-rendezvous` binary keeps relaying
packets for that session's lifetime, but this is a bandwidth-forwarding
function, architecturally distinct from "the server the app depends on."

### 3.2 What this means for implementation

- `ezpeek-rendezvous` must be **horizontally trivial to self-host**: a
  single binary, a config file with a domain/IP and a shared secret or
  pairing-code scheme, no database requirement for v1 (in-memory session
  state keyed by pairing code, TTL'd). Users should be able to run their own
  instead of depending on a hosted one; support this as a first-class
  deployment mode, not an afterthought.
- Do not add any code path where `ezpeek-host` or `ezpeek-viewer` send
  encoded frames to `ezpeek-rendezvous` for anything other than TURN relay
  of the already-encrypted SRTP stream. There is no "upload to server, view
  from anywhere" mode — this is a direct remote-desktop tool, not a
  broadcast/relay service.
- Metrics/telemetry: if added later, must go over the DataChannel directly
  between peers, or be purely local, never through rendezvous.
- Local network (LAN) sessions should be able to skip rendezvous entirely
  once peers already know each other's address (e.g. mDNS/local discovery
  for "same subnet" use) — treat rendezvous as being specifically for the
  "peers don't already know how to reach each other, and/or are behind NAT"
  case, not a mandatory hop for every session regardless of topology. This
  is a stretch goal past the milestone list in §12, not a blocker for v1.

## 4. Architecture (media pipeline)

Pipeline, one direction (host → viewer for video; viewer → host for input,
out of band). This is unaffected by whether the underlying connection ended
up direct or TURN-relayed — that's transport-layer detail the pipeline above
it doesn't need to know about.

```
 [GPU Framebuffer]
        │  zero-copy handle (D3D11 texture / DMA-BUF / IOSurface)
        ▼
 ┌─────────────┐
 │  Capture    │  platform-specific, behind `CaptureSource` trait
 └─────┬───────┘
       │ GPU-resident frame (no CPU map unless encoder truly requires it)
       ▼
 ┌─────────────┐
 │  Encoder    │  NVENC / AMF / QSV / VideoToolbox, behind `Encoder` trait
 └─────┬───────┘
       │ Annex-B / OBU bitstream + timestamps
       ▼
 ┌─────────────┐
 │  Transport  │  WebRTC peer connection (SRTP over DTLS, ICE, congestion
 │  (host)     │  control, NACK/FEC, adaptive bitrate) — direct by default,
 │             │  relayed through ezpeek-rendezvous only if ICE requires it
 └─────┬───────┘
       │ network — direct P2P path, or TURN relay as fallback (§3)
       ▼
 ┌─────────────┐
 │  Transport  │  jitter buffer → depacketize
 │  (viewer)   │
 └─────┬───────┘
       ▼
 ┌─────────────┐
 │  Decoder    │  hardware decode, behind `Decoder` trait
 └─────┬───────┘
       │ GPU-resident decoded surface
       ▼
 ┌─────────────┐
 │  Presenter  │  swap chain / EGL / Metal, present without CPU copy
 └─────────────┘
```

Input travels the opposite direction over the same WebRTC connection's
`DataChannel` (SCTP, ordered, low-volume) — mouse/keyboard/gamepad events
are small and latency-sensitive but not bandwidth-sensitive, so they get
their own channel rather than being multiplexed with video RTP.

### Zero-copy requirement (the whole point of this project)

Every stage above must avoid a CPU-side pixel copy unless the platform
genuinely leaves no alternative. Concretely:

- **Windows**: DXGI Desktop Duplication yields an `ID3D11Texture2D`. Hand
  that texture directly to NVENC's D3D11 input mode, or to AMF/QSV via the
  same D3D11 device — do not call `Map`/`CopyResource` to system memory.
- **Linux**: PipeWire delivers a DMA-BUF fd. Import it into VAAPI via
  `vaCreateSurfaces`/`VASurfaceAttribExternalBuffers`, or into NVENC via
  CUDA's EGL/DRM interop. Same rule: no `glReadPixels`/CPU staging buffer.
- **macOS**: ScreenCaptureKit delivers an `IOSurface`-backed `CVPixelBuffer`.
  Wrap it directly for `VTCompressionSession` input.
- **Decode side**, symmetric: keep the decoded surface as a GPU texture
  (`ID3D11Texture2D` / `VASurfaceID` / `CVPixelBuffer`) and present it
  straight to the swap chain / layer. A CPU round trip on either end of the
  pipe will dominate the latency budget and defeats the design — treat it as
  a bug, not a shortcut, if one shows up in profiling.

## 5. Tech Stack

- **Language**: Rust (2021 edition), workspace of crates. Rationale: safe
  FFI boundary tooling, `unsafe` is opt-in and auditable, no GC pause in the
  hot path, first-class cross-platform build tooling.
- **Async runtime**: `tokio`, used for transport/rendezvous/control-plane
  only. The capture→encode hot path runs on dedicated OS threads with
  real-time-ish scheduling hints, not on the async executor.
- **GPU/codec FFI**: thin `-sys` crates per vendor SDK:
  - `nvenc-sys` / `nvdec-sys` — NVIDIA Video Codec SDK bindings.
  - `amf-sys` — AMD Advanced Media Framework bindings.
  - `vaapi-sys` — libva bindings (Intel + AMD on Linux).
  - macOS: `objc2` + `objc2-video-toolbox` / `objc2-screen-capture-kit`
    bindings rather than hand-rolled `extern "C"`.
  - `windows` crate (official `microsoft/windows-rs`) for DXGI/D3D11.
- **Transport**: WebRTC, via `webrtc-rs` (pure Rust) as the default target.
  This buys DTLS-SRTP encryption, the ICE agent (STUN/TURN/host candidate
  gathering and connectivity checks — i.e. the P2P-first behavior in §3 is
  this library's job, not something to hand-roll), congestion control
  (GCC/BBR-style), NACK + FEC, and RTP payloads for H.264 (RFC 6184) and AV1
  (AOM's RTP payload spec). Do not build a bespoke UDP+QUIC protocol for
  v1 — it is more work for a worse starting point on NAT traversal and
  congestion control, both of which are central to this architecture.
- **Rendezvous/relay**: `ezpeek-rendezvous` is an `axum` WebSocket service
  for the handshake, plus an embedded TURN server for the relay fallback —
  either shell out to / embed `coturn`, or use a Rust TURN implementation if
  one in the `webrtc-rs` ecosystem is suitable; evaluate both before
  committing, since this is new-in-this-revision scope. Whichever is chosen,
  it must support standard TURN (RFC 5766) so any spec-compliant STUN/TURN
  server (including third-party or cloud ones) can substitute for it — don't
  invent a proprietary relay protocol.
- **Handshake payload**: SDP offer/answer plus a custom JSON capability
  payload (supported codecs, max resolution/fps, HDR flag) so peers
  negotiate the best mutually supported codec — prefer AV1 if both sides
  have hardware AV1 encode/decode and bandwidth headroom, else H.264. See
  §7.
- **Software codec fallback** (compatibility path only, off the hot path by
  default): `dav1d` for AV1 decode and `openh264`/`x264` for H.264, gated
  behind a `software-fallback` feature flag, used only when no hardware
  decoder is present. Never silently fall back — surface it in diagnostics,
  since it changes the latency contract.
- **Input injection**: `SendInput` (Windows), `uinput` (Linux), `CGEvent`
  (macOS), behind an `InputInjector` trait.
- **UI shell**: minimal — `egui` for local status/settings windows on both
  peer binaries. Not a priority versus the pipeline itself.

## 6. Repository / crate layout

```
ezpeek/
├── AGENTS.md
├── Cargo.toml                 # workspace root
├── crates/
│   ├── ezpeek-core/            # frame types, pixel formats, timing, errors
│   ├── ezpeek-capture/         # CaptureSource trait + platform backends
│   │   ├── dxgi/
│   │   ├── pipewire/
│   │   └── screencapturekit/
│   ├── ezpeek-encode/          # Encoder trait + vendor backends
│   │   ├── nvenc/
│   │   ├── amf/
│   │   ├── vaapi/
│   │   ├── videotoolbox/
│   │   └── software/           # feature-gated fallback (dav1d/x264)
│   ├── ezpeek-decode/          # Decoder trait, mirrors ezpeek-encode
│   ├── ezpeek-transport/       # WebRTC peer connection, ICE config, RTP
│   │                           # pack/unpack, DataChannel — this is where
│   │                           # the ICE server list (STUN + TURN via
│   │                           # ezpeek-rendezvous) gets wired up
│   ├── ezpeek-handshake/       # rendezvous WebSocket client: pairing/auth,
│   │                           # SDP + capability exchange (used by both
│   │                           # ezpeek-host and ezpeek-viewer)
│   ├── ezpeek-input/           # InputInjector trait + platform backends
│   ├── ezpeek-present/         # swap chain / EGL / Metal presenter
│   └── ezpeek-gui/             # shared egui panels
├── bin/
│   ├── ezpeek-host/             # peer: machine being remoted into
│   ├── ezpeek-viewer/           # peer: viewing machine
│   └── ezpeek-rendezvous/       # infra: handshake relay + fallback TURN
└── tests/
    ├── integration/             # hardware-gated, see §9
    └── synthetic/                # no-GPU deterministic test capture source
```

Cross-cutting rule: platform-specific `unsafe` FFI stays inside the
`*-sys`/vendor-backend modules. Trait definitions in `ezpeek-capture`,
`ezpeek-encode`, `ezpeek-decode`, `ezpeek-input` are safe Rust; nothing
above those boundaries should need `unsafe`.

## 7. Codec & capability negotiation

Carried inside the rendezvous handshake (§3.1 step 1). At session start,
both peers report, per codec:
- hardware encode available (y/n), hardware decode available (y/n)
- max resolution/framerate the encoder can sustain
- current estimated available upstream bandwidth

Selection policy (implement exactly this, don't invent a new heuristic):
1. If both sides have hardware AV1 encode **and** decode, and estimated
   bandwidth supports the target resolution/fps at AV1's typical bitrate
   savings, use AV1.
2. Otherwise use H.264 (must always be available — treat H.264 hardware
   support as a hard baseline requirement for both peer binaries).
3. Re-evaluate on renegotiation triggers (resolution change, sustained
   packet loss/bandwidth drop) — don't hardcode the choice for the session.

## 8. Latency budget

Target end-to-end (glass-to-glass) on a LAN or a direct P2P internet path:
**≤ 16 ms** on LAN, **≤ 50 ms** typical direct internet path. Rough
per-stage budget to design against:

| Stage | Budget |
|---|---|
| Capture (frame available → GPU handle ready) | 1–2 ms |
| Encode | 2–6 ms (H.264 low-latency preset faster than AV1) |
| Network (one-way, direct P2P) | 5–20 ms, network-dependent |
| Jitter buffer / depacketize | 0–3 ms (keep minimal — this is the easiest place for careless code to add latency) |
| Decode | 1–4 ms |
| Present (bounded by display refresh) | 1–16 ms |

When a session falls back to TURN relay (§3), add the relay's one-way
transit time (rendezvous host's network position now sits on the media
path) — budget this as a distinct, worse-case scenario in benchmarks rather
than folding it into the primary target above; a relayed session is expected
to be noticeably higher latency and that's an acceptable, clearly-labeled
degradation, not a bug.

Encoder configuration must reflect the primary target: **zero or minimal
B-frames, short GOP with frequent intra refresh (not full IDR unless
requested) for loss resilience, CBR or low-latency VBR rate control,
encoder "low-latency" / "ultra-low-latency" preset**, not the
quality-oriented presets meant for offline encoding.

## 9. Testing & benchmarking requirements

- **Unit tests**: protocol serialization, capability negotiation logic,
  RTP pack/unpack — run in CI, no GPU required.
- **Synthetic pipeline tests**: a `CaptureSource` implementation in
  `tests/synthetic` that generates deterministic patterned frames (e.g. a
  moving test card with embedded timestamps) so encode→decode round trips
  and transport loss/reorder handling can be tested without real hardware.
  This must run in CI on every PR.
- **NAT/connectivity tests**: exercise `ezpeek-transport` + `ezpeek-handshake`
  against simulated NAT topologies (e.g. via network namespaces or a NAT
  simulator) to confirm (a) direct connection succeeds when it should, and
  (b) fallback to `ezpeek-rendezvous`'s TURN relay succeeds when direct
  candidates are deliberately blocked. Both paths need coverage — a PR that
  only tests the happy direct-P2P path hasn't tested the architecture.
- **Hardware integration tests**: live under `tests/integration`, marked
  `#[ignore]` by default, gated behind a `--features hw-tests` flag and a
  runtime hardware-capability check. These are not expected to run in a
  generic CI runner — document in the test file which GPU/vendor path it
  exercises.
- **Latency benchmark harness**: inject a timestamp into a captured frame,
  measure wall-clock time until that frame is presented on the receiving
  end (loopback on one machine is an acceptable first pass). Run it once for
  the direct-P2P path and once for the forced-relay path, and report both —
  see §8. Any change touching capture/encode/transport/decode/present must
  not regress the direct-path benchmark — report before/after numbers in the
  PR description.
- Do not add sleeps, polling loops, or buffering "for safety" in the hot
  path to make tests pass. If a test is flaky because of real timing
  sensitivity, fix the synchronization primitive, don't paper over it.

## 10. Security considerations

- Transport encryption comes from WebRTC's mandatory DTLS-SRTP, negotiated
  **directly between `ezpeek-host` and `ezpeek-viewer`** — do not disable or
  downgrade it, and do not architect anything where `ezpeek-rendezvous`
  holds or brokers the DTLS keys. When relay is used, `ezpeek-rendezvous`
  forwards ciphertext it cannot decrypt; it must never be in a position to
  see plaintext frames, and code review should treat any change that would
  let it terminate DTLS as a critical-severity issue, not a refactor.
- Rendezvous is still a trust boundary worth taking seriously even though it
  can't see media: it can see both peers' public IPs and connection timing,
  and (during relay) encrypted packet volume/timing. Document this in
  user-facing material; don't overclaim "the server can't see anything about
  your session."
- Pairing/auth: session establishment requires a short-lived pairing code
  or an existing trusted device keypair, verified during the
  `ezpeek-handshake` exchange; `ezpeek-rendezvous` must not allow an
  unauthenticated peer to open a session or consume relay bandwidth.
- Never log frame pixel contents or full input-event payloads at default
  log levels; debug-level logging of event *types* (not content/keystrokes)
  is fine.
- Rate-limit and back off on repeated failed pairing/handshake attempts at
  `ezpeek-rendezvous`, and cap relay bandwidth/session count to prevent a
  self-hosted rendezvous instance from being used as an open relay by
  unrelated third parties.
- Input injection backends must not run with more privilege than the
  session actually needs — no broad "run as SYSTEM/root" default.

## 11. Coding conventions

- `rustfmt` default settings, `clippy` clean (`-D warnings` in CI).
- Error handling: `thiserror` for library-crate error enums, `anyhow` only
  in the three `bin/` binaries.
- No allocation in the steady-state per-frame path where avoidable — use
  pre-allocated frame/packet pools (`ezpeek-core` provides the pool types).
- Every platform backend module must have a doc comment at the top stating
  which OS/GPU-vendor combination it targets and linking the vendor SDK
  version it was written against.
- When adding a new backend, implement the existing trait
  (`CaptureSource`/`Encoder`/`Decoder`/`InputInjector`) — do not special-case
  a new backend into the pipeline orchestration code in `bin/`.
- Anything that would make `ezpeek-rendezvous` a required steady-state hop
  for media, rather than a handshake + fallback-relay component, is a
  design regression — flag it in review rather than merging it, even if it
  simplifies the code.

## 12. Milestones (build in this order)

1. Windows-only vertical slice: DXGI capture → NVENC H.264 → local loopback
   WebRTC connection on one machine → NVDEC decode → present. Proves the
   zero-copy path end-to-end before anything else; no networking yet.
2. Stand up `ezpeek-rendezvous` (handshake only, no TURN yet) and get
   `ezpeek-host`/`ezpeek-viewer` doing a **direct P2P connection** between
   two machines on the same LAN via the handshake exchange.
3. Add STUN-based direct connectivity across NAT (two machines on different
   networks, direct candidate succeeds) — this is the primary target
   experience and should be validated before relay work starts.
4. Add TURN relay fallback in `ezpeek-rendezvous` and the connectivity test
   coverage from §9 (forced-relay scenario) to prove the CGNAT/symmetric-NAT
   fallback actually works.
5. Add AV1 codec path (encode + decode + negotiation) on Windows.
6. Add Linux backend (PipeWire capture, VAAPI/NVENC encode).
7. Add macOS backend (ScreenCaptureKit, VideoToolbox).
8. Input injection both directions, all platforms.
9. Adaptive bitrate / codec renegotiation under changing network conditions.
10. Software fallback path, pairing/auth hardening, polish.

Do not jump ahead to cross-platform work before milestone 1's latency
benchmark hits the §8 target on one platform, and do not build TURN relay
(milestone 4) before direct P2P (milestones 2–3) is working end to end —
relay is the fallback, and building it first inverts the architecture's
priorities.
