use ezpeek_core::{EzpeekError, GpuFrame, VideoCodec};

pub mod amf;
pub mod nvenc;
pub mod vaapi;
pub mod videotoolbox;
pub mod x264;

#[cfg(feature = "software-fallback")]
pub mod software;

#[derive(Debug, Clone)]
pub struct BitstreamChunk {
    pub codec: VideoCodec,
    pub data: Vec<u8>,
    pub timestamp_ns: u64,
    pub seq: u64,
    pub is_keyframe: bool,
}

#[derive(Debug, Clone, Copy)]
pub struct LowLatencyConfig {
    pub max_b_frames: u32,
    pub gop_len: u32,
    pub intra_refresh: bool,
    pub bitrate_bps: u64,
}

impl Default for LowLatencyConfig {
    fn default() -> Self {
        Self {
            max_b_frames: 0,
            gop_len: 60,
            intra_refresh: true,
            bitrate_bps: 8_000_000,
        }
    }
}

pub trait Encoder {
    fn encode(&mut self, frame: &GpuFrame) -> Result<BitstreamChunk, EzpeekError>;
    fn configure_low_latency(&mut self, cfg: &LowLatencyConfig) -> Result<(), EzpeekError>;
    fn codec(&self) -> VideoCodec {
        VideoCodec::H264
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Renegotiate {
    Keep,
    SwitchCodec(VideoCodec),
    LowerBitrate(u64),
}

pub fn renegotiate_trigger(
    current: VideoCodec,
    packet_loss_pct: f64,
    rtt_ms: u64,
    bitrate_bps: u64,
) -> Renegotiate {
    if packet_loss_pct > 10.0 || rtt_ms > 150 {
        if current == VideoCodec::Av1 {
            return Renegotiate::SwitchCodec(VideoCodec::H264);
        }
        return Renegotiate::LowerBitrate(bitrate_bps / 2);
    }
    if packet_loss_pct > 5.0 || rtt_ms > 80 {
        return Renegotiate::LowerBitrate((bitrate_bps * 3) / 4);
    }
    Renegotiate::Keep
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EncoderBackend {
    Nvenc,
    Vaapi,
    Amf,
    VideoToolbox,
    X264,
}

impl EncoderBackend {
    pub fn platform_order() -> &'static [Self] {
        #[cfg(target_os = "linux")]
        return &[Self::Nvenc, Self::Vaapi, Self::X264];
        #[cfg(target_os = "windows")]
        return &[Self::Nvenc, Self::Amf, Self::X264];
        #[cfg(target_os = "macos")]
        return &[Self::VideoToolbox, Self::X264];
        #[cfg(not(any(target_os = "linux", target_os = "windows", target_os = "macos")))]
        return &[Self::X264];
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Nvenc => "nvenc",
            Self::Vaapi => "vaapi",
            Self::Amf => "amf",
            Self::VideoToolbox => "videotoolbox",
            Self::X264 => "x264",
        }
    }

    pub fn is_hardware(self) -> bool {
        !matches!(self, Self::X264)
    }
}

pub fn low_latency_cbr(bitrate_bps: u64) -> LowLatencyConfig {
    LowLatencyConfig {
        bitrate_bps,
        ..Default::default()
    }
}

pub fn warn_fallback_in_use(codec: &str) {
    eprintln!("warning: using software {codec} encoder fallback; latency contract changed");
}
