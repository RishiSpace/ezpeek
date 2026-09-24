use anyhow::{bail, Result};

#[cfg(feature = "nvenc")]
use ezpeek_capture::CaptureSource;
use ezpeek_core::VideoCodec;
use ezpeek_decode::{openh264::OpenH264Decoder, Decoder};
use ezpeek_present::{software::SoftwarePresenter, Presenter};

fn parse_arg(args: &[String], name: &str) -> Option<String> {
    args.windows(2).find(|w| w[0] == name).map(|w| w[1].clone())
}

fn parse_u32(args: &[String], name: &str, default: u32) -> u32 {
    parse_arg(args, name)
        .and_then(|s| s.parse().ok())
        .unwrap_or(default)
}

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|a| a == "--help" || a == "-h") {
        println!("ezpeek-viewer: receive, decode, present; forward local input.");
        println!("  --rendezvous-url URL --pairing-code CODE --codec auto|h264|av1");
        println!("  --loopback-synthetic (encode synthetic locally, decode+present: full local pipeline)");
        println!("  --in FILE (decode Annex B file and present)");
        println!("  --width W --height H --fps FPS --frames N");
        return Ok(());
    }

    let width = parse_u32(&args, "--width", 640);
    let height = parse_u32(&args, "--height", 480);
    let fps = parse_u32(&args, "--fps", 30);
    let frames = parse_u32(&args, "--frames", 60);

    if let Some(path) = parse_arg(&args, "--in") {
        return play_file(&path, width, height);
    }
    if args.iter().any(|a| a == "--loopback-synthetic") {
        return loopback_synthetic(width, height, fps, frames);
    }
    bail!("no source: pass --loopback-synthetic or --in FILE (network receive lands in item 7)")
}

fn play_file(path: &str, width: u32, height: u32) -> Result<()> {
    let data = std::fs::read(path)?;
    let aus = split_access_units(&data);
    if aus.is_empty() {
        bail!("no access units in {path}");
    }
    let mut dec = OpenH264Decoder::new(width, height);
    let mut presenter = SoftwarePresenter::new(width as usize, height as usize)?;
    let mut shown = 0u32;
    for (i, au) in aus.iter().enumerate() {
        let chunk = ezpeek_decode::BitstreamChunk {
            codec: VideoCodec::H264,
            data: au.clone(),
            timestamp_ns: ezpeek_core::now_ns(),
            seq: i as u64,
            is_keyframe: i == 0,
        };
        match dec.decode(&chunk) {
            Ok(surface) => {
                presenter.present(&surface)?;
                shown += 1;
            }
            Err(e) => eprintln!("decode au {i}: {e:#}"),
        }
    }
    eprintln!("presented {shown} frames from {path}");
    Ok(())
}

fn loopback_synthetic(width: u32, height: u32, fps: u32, frames: u32) -> Result<()> {
    #[cfg(feature = "nvenc")]
    {
        loopback_with_encoder(
            &mut ezpeek_encode::nvenc::NvencEncoder::new(width, height, fps),
            width,
            height,
            fps,
            frames,
        )
    }
    #[cfg(not(feature = "nvenc"))]
    {
        let _ = (width, height, fps, frames);
        bail!("loopback needs --features nvenc (or wire x264 here)")
    }
}

#[cfg(feature = "nvenc")]
fn loopback_with_encoder(
    enc: &mut dyn ezpeek_encode::Encoder,
    width: u32,
    height: u32,
    fps: u32,
    frames: u32,
) -> Result<()> {
    enc.configure_low_latency(&ezpeek_encode::low_latency_cbr(8_000_000))?;
    let mut cap = ezpeek_synthetic::SyntheticCapture::new(width, height);
    let mut dec = OpenH264Decoder::new(width, height);
    let mut presenter = SoftwarePresenter::new(width as usize, height as usize)?;
    let frame_interval_ns = 1_000_000_000u64 / fps.max(1) as u64;
    let t0 = ezpeek_core::now_ns();
    let mut shown = 0u32;
    for i in 0..frames {
        let mut frame = cap.next_frame()?;
        frame.timestamp_ns = t0 + i as u64 * frame_interval_ns;
        frame.seq = i as u64;
        let chunk = enc.encode(&frame)?;
        match dec.decode(&chunk) {
            Ok(surface) => {
                presenter.present(&surface)?;
                shown += 1;
            }
            Err(e) => eprintln!("frame {i}: decode: {e:#}"),
        }
    }
    eprintln!("loopback: presented {shown}/{frames}");
    Ok(())
}

fn split_access_units(data: &[u8]) -> Vec<Vec<u8>> {
    let mut aus: Vec<Vec<u8>> = Vec::new();
    let mut cur: Vec<u8> = Vec::new();
    let mut i = 0;
    while i + 4 <= data.len() {
        if data[i] == 0 && data[i + 1] == 0 && data[i + 2] == 0 && data[i + 3] == 1 {
            if !cur.is_empty() {
                aus.push(std::mem::take(&mut cur));
            }
            cur.extend_from_slice(&data[i..i + 4]);
            i += 4;
        } else {
            cur.push(data[i]);
            i += 1;
        }
    }
    if !cur.is_empty() {
        aus.push(cur);
    }
    aus
}
