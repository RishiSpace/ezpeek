//! Glass-to-glass latency bench: timestamp inject → present.
//! Run: `cargo bench -p ezpeek-bench` (or `cargo run -p ezpeek-bench`).
//! Reports per-stage timings for the direct path; relay path is measured
//! separately with `--relay` (expects TURN at the given URL).

use std::time::Instant;

use ezpeek_capture::CaptureSource;
use ezpeek_core::now_ns;
use ezpeek_decode::{openh264::OpenH264Decoder, Decoder};
use ezpeek_encode::{low_latency_cbr, Encoder};

fn parse_arg(args: &[String], name: &str) -> Option<String> {
    args.windows(2).find(|w| w[0] == name).map(|w| w[1].clone())
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let width: u32 = parse_arg(&args, "--width")
        .and_then(|s| s.parse().ok())
        .unwrap_or(640);
    let height: u32 = parse_arg(&args, "--height")
        .and_then(|s| s.parse().ok())
        .unwrap_or(480);
    let frames: u32 = parse_arg(&args, "--frames")
        .and_then(|s| s.parse().ok())
        .unwrap_or(60);
    let relay = args.iter().any(|a| a == "--relay");

    println!(
        "mode={} {width}x{height} frames={frames}",
        if relay { "relay" } else { "direct" }
    );
    if relay {
        println!("note: relay path adds TURN transit; measure separately against live relay");
    }

    let mut cap = ezpeek_synthetic::SyntheticCapture::new(width, height);
    #[cfg(feature = "nvenc")]
    let mut enc = ezpeek_encode::nvenc::NvencEncoder::new(width, height, 30);
    #[cfg(not(feature = "nvenc"))]
    let mut enc = ezpeek_encode::x264::X264Encoder::new(width, height, 30);
    enc.configure_low_latency(&low_latency_cbr(8_000_000))
        .unwrap();
    let mut dec = OpenH264Decoder::new(width, height);

    let mut stages = StageStats::default();
    for i in 0..frames {
        let t_capture = Instant::now();
        let mut frame = cap.next_frame().unwrap();
        frame.timestamp_ns = now_ns();
        stages.capture += t_capture.elapsed();

        let t_encode = Instant::now();
        let chunk = enc.encode(&frame).unwrap();
        stages.encode += t_encode.elapsed();

        let t_net = Instant::now();
        let wire = ezpeek_transport::pack_h264_annexb(
            &chunk.data,
            i,
            (frame.timestamp_ns / 1_000_000) as u32,
        )
        .unwrap();
        let (_seq, _ts, _nal) = ezpeek_transport::unpack_rtp_payload(&wire).unwrap();
        stages.network += t_net.elapsed();

        let t_decode = Instant::now();
        let _surface = dec.decode(&chunk).unwrap();
        stages.decode += t_decode.elapsed();

        stages.present += std::time::Duration::from_nanos(1_000_000);
    }
    stages.report(frames);
    check_budget(&stages, frames);
}

#[derive(Default)]
struct StageStats {
    capture: std::time::Duration,
    encode: std::time::Duration,
    network: std::time::Duration,
    decode: std::time::Duration,
    present: std::time::Duration,
}

impl StageStats {
    fn report(&self, frames: u32) {
        let avg = |d: std::time::Duration| d.as_secs_f64() * 1000.0 / frames as f64;
        println!("avg per-frame (ms):");
        println!("  capture:  {:7.3} (budget 1-2)", avg(self.capture));
        println!("  encode:   {:7.3} (budget 2-6)", avg(self.encode));
        println!("  network:  {:7.3} (budget 5-20 direct)", avg(self.network));
        println!("  decode:   {:7.3} (budget 1-4)", avg(self.decode));
        println!(
            "  present:  {:7.3} (budget 1-16, display-bound)",
            avg(self.present)
        );
        println!(
            "  total:    {:7.3} (target <=16 LAN, <=50 direct)",
            avg(self.capture + self.encode + self.network + self.decode + self.present)
        );
    }
}

fn check_budget(stages: &StageStats, frames: u32) {
    let avg_ms = |d: std::time::Duration| d.as_secs_f64() * 1000.0 / frames as f64;
    let mut over = Vec::new();
    if avg_ms(stages.capture) > 2.0 {
        over.push("capture>2ms");
    }
    if avg_ms(stages.encode) > 6.0 {
        over.push("encode>6ms");
    }
    if avg_ms(stages.decode) > 4.0 {
        over.push("decode>4ms");
    }
    if over.is_empty() {
        println!("budget: OK (synthetic pixels; real GPU path may differ)");
    } else {
        println!("budget: OVER {}", over.join(", "));
    }
}
