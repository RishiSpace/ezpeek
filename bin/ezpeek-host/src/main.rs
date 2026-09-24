use anyhow::{bail, Result};

use ezpeek_capture::{CaptureBackend, CaptureSource};
use ezpeek_core::{now_ns, FrameHandle, GpuFrame, PixelFormat};
use ezpeek_encode::{Encoder, EncoderBackend};

fn parse_arg(args: &[String], name: &str) -> Option<String> {
    args.windows(2).find(|w| w[0] == name).map(|w| w[1].clone())
}

fn parse_u32(args: &[String], name: &str, default: u32) -> u32 {
    parse_arg(args, name)
        .and_then(|s| s.parse().ok())
        .unwrap_or(default)
}

#[derive(Clone)]
struct RunSpec {
    synthetic: bool,
    width: u32,
    height: u32,
    fps: u32,
    frames: u32,
    node_id: Option<u32>,
    #[allow(dead_code)]
    codec_idx: u8,
    _no_copy: std::marker::PhantomData<*const ()>,
}

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|a| a == "--help" || a == "-h") {
        println!("ezpeek-host: capture, encode, transmit; inject received input.");
        println!("  --rendezvous-url URL --pairing-code CODE --codec auto|h264|av1");
        println!("  --capture pipewire|dxgi|screencapturekit|synthetic (default: platform)");
        println!("  --node-id N (PipeWire node: skips portal dialog; use with --capture pipewire)");
        println!(
            "  --portal (open portal ScreenCast dialog to pick source; requires portal feature)"
        );
        println!("  --encoder vaapi|nvenc|x264|auto (default: auto = HW first, x264 last)");
        println!("  --width W --height H --fps FPS --frames N (default 1280x720@30, 60 frames)");
        println!("  --out FILE (write raw H.264 Annex B bitstream for verification)");
        return Ok(());
    }

    let capture_name = parse_arg(&args, "--capture")
        .unwrap_or_else(|| CaptureBackend::platform_default().as_str().to_string());
    let capture = match capture_name.as_str() {
        "pipewire" => CaptureBackend::PipeWire,
        "dxgi" => CaptureBackend::Dxgi,
        "screencapturekit" => CaptureBackend::ScreenCaptureKit,
        "synthetic" => CaptureBackend::PipeWire,
        other => bail!("unknown --capture backend: {other}"),
    };
    let codec_name = parse_arg(&args, "--codec").unwrap_or_else(|| "auto".to_string());
    let codec_idx: u8 = match codec_name.as_str() {
        "h264" => 1,
        "av1" => 2,
        _ => 0,
    };
    let spec = RunSpec {
        _no_copy: std::marker::PhantomData,
        synthetic: capture_name == "synthetic",
        width: parse_u32(&args, "--width", 1280),
        height: parse_u32(&args, "--height", 720),
        fps: parse_u32(&args, "--fps", 30),
        frames: parse_u32(&args, "--frames", 60),
        node_id: parse_arg(&args, "--node-id").and_then(|s| s.parse().ok()),
        codec_idx,
    };
    let out = parse_arg(&args, "--out");
    let use_portal = args.iter().any(|a| a == "--portal");

    let encoder_name = parse_arg(&args, "--encoder").unwrap_or_else(|| "auto".to_string());
    let order: Vec<EncoderBackend> = if encoder_name == "auto" {
        EncoderBackend::platform_order().to_vec()
    } else {
        match encoder_name.as_str() {
            "vaapi" => vec![EncoderBackend::Vaapi],
            "nvenc" => vec![EncoderBackend::Nvenc],
            "amf" => vec![EncoderBackend::Amf],
            "videotoolbox" => vec![EncoderBackend::VideoToolbox],
            "x264" => vec![EncoderBackend::X264],
            other => bail!("unknown --encoder backend: {other}"),
        }
    };

    eprintln!(
        "capture={} encoder-preference={:?} {}x{}@{} frames={} codec={}",
        if spec.synthetic {
            "synthetic"
        } else {
            capture.as_str()
        },
        order.iter().map(|b| b.as_str()).collect::<Vec<_>>(),
        spec.width,
        spec.height,
        spec.fps,
        spec.frames,
        codec_name
    );

    let spec = resolve_capture_source(spec, capture, use_portal)?;

    let mut backend_errs = Vec::new();
    for backend in &order {
        match try_encode_run(*backend, spec.clone(), out.as_deref()) {
            Ok(selected) => {
                eprintln!("selected encoder: {selected}");
                return Ok(());
            }
            Err(e) => {
                eprintln!("encoder {} failed: {e:#}", backend.as_str());
                backend_errs.push(format!("{}: {e:#}", backend.as_str()));
            }
        }
    }
    bail!("no encoder available: {}", backend_errs.join("; "))
}

fn resolve_capture_source(
    #[allow(unused_mut)] mut spec: RunSpec,
    capture: CaptureBackend,
    use_portal: bool,
) -> Result<RunSpec> {
    if spec.synthetic || capture != CaptureBackend::PipeWire {
        return Ok(spec);
    }
    if let Some(node) = spec.node_id {
        eprintln!("pipewire: using node {node} (portal dialog skipped)");
        return Ok(spec);
    }
    if !use_portal {
        bail!("pipewire live capture needs --node-id N or --portal (or --capture synthetic)");
    }
    #[cfg(feature = "portal")]
    {
        let session = tokio_runtime_block_on(ezpeek_capture::portal::request_screencast())?;
        let node = session
            .node_ids
            .into_iter()
            .next()
            .ok_or_else(|| anyhow::anyhow!("portal returned no streams"))?;
        eprintln!(
            "portal: selected node {node} {}x{}",
            session.width, session.height
        );
        spec.node_id = Some(node);
        spec.width = session.width;
        spec.height = session.height;
        Ok(spec)
    }
    #[cfg(not(feature = "portal"))]
    {
        bail!("--portal needs --features portal (or pass --node-id N)")
    }
}

#[cfg(feature = "portal")]
fn tokio_runtime_block_on<T>(
    f: impl std::future::Future<Output = Result<T, ezpeek_core::EzpeekError>>,
) -> Result<T> {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| anyhow::anyhow!("tokio: {e}"))?
        .block_on(f)
        .map_err(|e| anyhow::anyhow!("portal: {e:#}"))
}

fn make_source(spec: RunSpec) -> Result<Box<dyn CaptureSource>> {
    if spec.synthetic {
        Ok(Box::new(ezpeek_synthetic::SyntheticCapture::new(
            spec.width,
            spec.height,
        )))
    } else {
        match CaptureBackend::platform_default() {
            #[cfg(feature = "pipewire-capture")]
            CaptureBackend::PipeWire => {
                let node = spec.node_id.ok_or_else(|| {
                    anyhow::anyhow!("pipewire: missing node id (use --node-id or --portal)")
                })?;
                let mut cap =
                    ezpeek_capture::pipewire::PipeWireCapture::new(spec.width, spec.height);
                cap.connect_node(node)?;
                Ok(Box::new(cap))
            }
            _ => Ok(Box::new(ezpeek_capture::dxgi::create())),
        }
    }
}

fn try_encode_run(
    backend: EncoderBackend,
    spec: RunSpec,
    #[allow(unused_variables)] out: Option<&str>,
) -> Result<&'static str> {
    #[allow(unused_mut, unused_variables)]
    let mut source = make_source(spec.clone())?;
    match backend {
        #[cfg(feature = "nvenc")]
        EncoderBackend::Nvenc => {
            let want_av1 = spec.codec_idx == 2;
            let mut enc = ezpeek_encode::nvenc::NvencEncoder::with_codec(
                spec.width,
                spec.height,
                spec.fps,
                want_av1,
            );
            enc.configure_low_latency(&ezpeek_encode::low_latency_cbr(8_000_000))?;
            run_frames(&mut enc, source.as_mut(), spec.clone(), out)?;
            Ok(if want_av1 {
                "nvenc-av1 (hardware)"
            } else {
                "nvenc (hardware)"
            })
        }
        #[cfg(not(feature = "nvenc"))]
        EncoderBackend::Nvenc => bail!("nvenc not enabled in this build"),
        #[cfg(feature = "vaapi")]
        EncoderBackend::Vaapi => {
            let mut enc =
                ezpeek_encode::vaapi::VaapiEncoder::new(spec.width, spec.height, spec.fps);
            enc.configure_low_latency(&ezpeek_encode::low_latency_cbr(8_000_000))?;
            run_frames(&mut enc, source.as_mut(), spec.clone(), out)?;
            Ok("vaapi (hardware)")
        }
        #[cfg(not(feature = "vaapi"))]
        EncoderBackend::Vaapi => bail!("vaapi not enabled in this build"),
        #[cfg(feature = "x264-fallback")]
        EncoderBackend::X264 => {
            let mut enc = ezpeek_encode::x264::X264Encoder::new(spec.width, spec.height, spec.fps);
            enc.configure_low_latency(&ezpeek_encode::low_latency_cbr(8_000_000))?;
            run_frames(&mut enc, source.as_mut(), spec.clone(), out)?;
            Ok("x264 (software fallback; latency contract changed)")
        }
        #[cfg(not(feature = "x264-fallback"))]
        EncoderBackend::X264 => bail!("x264 not enabled in this build"),
        _ => bail!("backend not supported on this platform"),
    }
}

#[allow(dead_code)]
fn run_frames(
    enc: &mut dyn Encoder,
    source: &mut dyn CaptureSource,
    spec: RunSpec,
    out: Option<&str>,
) -> Result<()> {
    use std::io::Write;
    let mut file = out
        .map(std::fs::File::create)
        .transpose()?
        .map(std::io::BufWriter::new);
    let frame_interval_ns = 1_000_000_000u64 / spec.fps.max(1) as u64;
    let t0 = now_ns();
    let mut total_bytes = 0u64;
    for i in 0..spec.frames {
        let frame = if spec.synthetic {
            moving_pattern(spec.clone(), i, t0 + i as u64 * frame_interval_ns)
        } else {
            source.next_frame()?
        };
        let chunk = enc.encode(&frame)?;
        total_bytes += chunk.data.len() as u64;
        if let Some(f) = file.as_mut() {
            if !chunk.data.starts_with(&[0, 0, 0, 1]) && !chunk.data.starts_with(&[0, 0, 1]) {
                f.write_all(&[0, 0, 0, 1])?;
            }
            f.write_all(&chunk.data)?;
        }
        if i == 0 || (i + 1) % 30 == 0 {
            eprintln!(
                "frame {i}: {} bytes key={} ts={}",
                chunk.data.len(),
                chunk.is_keyframe,
                chunk.timestamp_ns
            );
        }
    }
    if let Some(f) = file.as_mut() {
        f.flush()?;
    }
    let dt = now_ns().saturating_sub(t0).max(1);
    eprintln!(
        "encoded {} frames, {total_bytes} bytes in {}ms ({:.1} fps)",
        spec.frames,
        dt / 1_000_000,
        spec.frames as f64 * 1e9 / dt as f64
    );
    Ok(())
}

#[allow(dead_code)]
fn moving_pattern(spec: RunSpec, i: u32, ts: u64) -> GpuFrame {
    GpuFrame {
        handle: FrameHandle::Synthetic((i * 8) as u64),
        width: spec.width,
        height: spec.height,
        format: PixelFormat::Nv12,
        timestamp_ns: ts,
        seq: i as u64,
    }
}
