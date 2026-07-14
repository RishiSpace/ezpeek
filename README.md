# ezpeek

**LAN remote desktop** with hardware-accelerated capture + encode and low-latency streaming (**FFmpeg + SRT + Qt**), plus optional **cloud auth / friends / reverse-proxy** via a separate server.

Cross-platform focus: **Linux Wayland** and **Windows**, with real mouse/keyboard remoting and an integrated viewer.

**Version:** 0.2.0

---

## Current status

| Area | Status |
|------|--------|
| **Same-LAN remoting** | Working — discovery, SRT video, TCP control, integrated Qt viewer |
| **Linux Wayland host** | Working — xdg-desktop-portal + PipeWire capture; RemoteDesktop portal for input |
| **Linux X11 host** | Legacy path retained for pure X11 sessions |
| **Windows host / view** | Working — gdigrab/d3d11grab where available; SendInput for control |
| **Codecs** | **Auto:** probe real HW **AV1**, else HW/soft **H.264**. Default **CBR ~25 Mbps**. Stream FPS follows min(host refresh, client refresh) |
| **Cloud auth + friends** | Working — login/register UI; server URL saved locally (no hardcoded default) |
| **Cloud TCP reverse-proxy** | Working — control **and video** over server TCP **8788** (both peers dial out; **no inbound ports on user PCs**) |
| **STUN / TURN** | Working on **[ezpeek-svr](https://github.com/RishiSpace/ezpeek-svr)** (UDP **3478**); clients fetch config via `GET /ice` |
| **Video over internet** | Same LAN → direct SRT; otherwise → cloud TCP video relay (+ STUN/TURN on the server for NAT helpers) |

---

## Features

- **Capture**
  - Linux: Wayland (PipeWire via portals); X11 for legacy sessions
  - Windows: gdigrab + d3d11grab when FFmpeg supports them
- **HW acceleration** — probes NVENC / AMF / QSV / VAAPI; falls back to libx264
- **Transport** — SRT (low latency, reliable) on UDP
- **Input remoting** — mouse + keyboard (Windows SendInput; Linux portal or xdotool fallback)
- **LAN discovery** — UDP broadcast (no mDNS required)
- **GUI** — PySide6: login → main window → integrated viewer with input grab
- **Cloud (optional)** — accounts, friends list, presence, reverse-proxy rendezvous

---

## Cloud server → [ezpeek-svr](https://github.com/RishiSpace/ezpeek-svr)

**Server-level hosting** (auth, friends, presence, TCP reverse-proxy, STUN/TURN) is provided by the **[ezpeek-svr](https://github.com/RishiSpace/ezpeek-svr)** repository — not this client tree.

| | |
|---|---|
| **Repo** | https://github.com/RishiSpace/ezpeek-svr |
| **API** | TCP **8787** (HTTP: register/login/friends/presence/**ice**) |
| **TCP relay** | TCP **8788** (host ↔ viewer: **control** + **video** channels) |
| **STUN/TURN** | UDP **3478** (+ optional high UDP range for TURN media) |

### Why this matters for ports

| Who | Ports to open |
|-----|----------------|
| **ezpeek-svr (VPS)** | **8787**, **8788**, **3478/udp** (and TURN media range if used) |
| **User PCs (cloud mode)** | **None inbound** — only outbound to the server |

Same-LAN sessions still use local UDP/TCP (discovery **27787**, SRT **2734**, control **2735**) for lowest latency; those are optional when both sides use cloud friends + relay.

Deploy ezpeek-svr on a VPS, open the server ports above, then point the client at `http://YOUR_HOST:8787` on the sign-in screen. The URL is stored in `~/.config/ezpeek/settings.json`.

A copy of the cloud package and STUN/TURN code also lives under [`server/`](server/) for local reference. **Production cloud hosting should use [ezpeek-svr](https://github.com/RishiSpace/ezpeek-svr).**

---

## Requirements

### Runtime
- **Python ≥ 3.10**
- **FFmpeg + ffplay** on `PATH` (SRT strongly recommended; PipeWire input for Linux Wayland hosts)
- **PySide6**, **zeroconf** (installed via pip)
- Linux: **dbus-python**, **PyGObject** (Wayland portals)
- Linux hosting: **xdg-desktop-portal** + backend (GNOME/KDE/wlr), **PipeWire** + wireplumber
- Linux (optional): **xdotool** for X11 / portal fallback input

### Install FFmpeg + ffplay

| Platform | Typical install |
|----------|-----------------|
| **Windows** | `winget install ffmpeg` (prefer full build), or Scoop/Chocolatey full packages, or [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) full zip + PATH |
| **Debian / Ubuntu / …** | `sudo apt install ffmpeg` |
| **Fedora** | Enable [RPM Fusion](https://rpmfusion.org/), then `sudo dnf swap ffmpeg-free ffmpeg --allowerasing` (or `dnf install ffmpeg`) |
| **Arch / Manjaro / …** | `sudo pacman -S ffmpeg` |

Verify:

```bash
ffmpeg -version
ffplay -version
ffmpeg -protocols 2>/dev/null | grep -i srt
```

---

## Ports

### Same LAN (open on host; both sides if strict)

| Port | Proto | Role |
|------|--------|------|
| **27787** | UDP | Peer discovery |
| **2734** | UDP | Video (SRT) |
| **2735** | TCP | Control / input |

### Cloud client (outbound only — no inbound)

| Port | Proto | Role |
|------|--------|------|
| **8787** | TCP | API (+ `/ice`) |
| **8788** | TCP | Control + video reverse-proxy |
| **3478** | UDP | STUN/TURN (outbound to server) |

### Server STUN/TURN (on the VPS, not on users)

| Port | Proto | Role |
|------|--------|------|
| **3478** | UDP | STUN + TURN |
| **49152–65535** | UDP | TURN media range (when TURN used) |

---

## Quick start (LAN — no cloud)

```bash
# Install client
pip install -e .

# Prove SRT + control on this machine (no second PC, no screen share)
ezpeek self-test

# GUI
ezpeek

# Host synthetic pattern (debug — no capture permissions)
ezpeek --test-pattern
```

### Two-machine checklist
1. Same network (LAN, bridged VMs, etc.).
2. Install client + FFmpeg/ffplay (SRT) on both machines.
3. Allow **UDP 27787**, **UDP 2734**, **TCP 2735**.
4. Machine A: `ezpeek` → **H** / Start Hosting (Wayland: accept screen-share portal). Status: `HOSTING … :2734` / ctrl **2735**.
5. Machine B: wait until A shows **video 2734** → **double-click**.
6. Viewer: enable **Grab Input** (ESC to release).

**Codec:** auto HW AV1 when the encoder actually works on the GPU; otherwise H.264 (HW if available, else libx264). **CBR ~25 Mbps.** Stream FPS adapts to host and client display refresh.

**Logs:** `~/.cache/ezpeek/logs/` (Linux) or `%LOCALAPPDATA%\ezpeek\logs\` (Windows).

---

## Quick start (with cloud friends)

1. Deploy **[ezpeek-svr](https://github.com/RishiSpace/ezpeek-svr)** and open **8787**, **8788**, and **3478/udp** (server only).
2. On each client, run `ezpeek` → enter **Server URL** (e.g. `http://YOUR_HOST:8787`) → register / log in.
3. Add friends by username.
4. Friend A: **H** to host (also starts outbound control+video relay agents).
5. Friend B: **Connect** — if not on the same LAN, ezpeek uses the **cloud TCP path** for video + input (no ports to open at home).

---

## Platform notes

### Wayland (default on modern Linux)
- Capture: xdg-desktop-portal + PipeWire via FFmpeg.
- Input: RemoteDesktop portal (one-time permission); xdotool only as fallback / X11.
- Needs FFmpeg with PipeWire support when hosting. If capture fails, try a fuller build (e.g. AUR `ffmpeg-full` / OBS-oriented builds).
- GUI runs natively on Wayland.

### Windows
- Prefer a **full** FFmpeg build (SRT + modern capture).
- HW encode (NVENC/AMF/…) auto-selected when probes succeed.
- Input via SendInput (normal user session is enough).

---

## Configuration / advanced

`HostService` supports (among others):
- `fps`, `bitrate_kbps`, `codec` (`auto` / `av1` / `h264` / `hevc`)
- `enable_control=True`
- `use_nat=True` (STUN public address advertisement when possible)
- `test_pattern=True` (synthetic source)

Client settings / session files live under the user config dir (`~/.config/ezpeek/` on Linux).

SRT latency is tuned for low delay (~20 ms target range; exact values in transport code).

---

## Architecture

```
[Host]  capture → FFmpeg encode (AV1/H.264 CBR)
          ├─ SRT listen :2734          (LAN peers)
          └─ TCP listen 127.0.0.1:12734 (cloud twin)
        ControlServer TCP :2735
        Outbound agents → ezpeek-svr :8788 (HOST control + HOST video)

[Viewer LAN]   SRT caller → host:2734 · control → host:2735

[Viewer cloud] Local tunnels 127.0.0.1:12734 / :12735
               dial VIEW on ezpeek-svr :8788 → paired to host agents
               FFmpeg TCP decode + ControlClient → localhost tunnels

[ezpeek-svr]  :8787 API  ·  :8788 TCP relay  ·  :3478 STUN/TURN
```

Modules: `ezpeek/core/{capture,encoder,transport,control,discovery,host,viewer,…}`, `ezpeek/gui/`, `ezpeek/cloud/`.

---

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest
ezpeek self-test
```

---

## Related repos

| Repo | Role |
|------|------|
| **[ezpeek](https://github.com/RishiSpace/ezpeek)** (this) | Client: LAN remoting + GUI + cloud client |
| **[ezpeek-svr](https://github.com/RishiSpace/ezpeek-svr)** | **Server hosting:** auth, friends, presence, TCP relay |

## License

See [LICENSE](LICENSE).

Contributions and issues welcome. Goal: reliable, smooth, hardware-accelerated remoting across Linux Wayland and Windows — with optional cloud identity when you need friends across networks.
