//! x264 software H.264 encoder backend (fallback when no GPU encoder).
//! Target: any OS/CPU; links libx264 (tested against 0.165).
//! Config: ultrafast + zerolatency tune, no B-frames, short GOP.
//! Never the default — hardware first, selected only when no HW
//! encoder is available and the caller opts in.
//! Feature: `x264-fallback`.

use ezpeek_core::{EzpeekError, GpuFrame};

use crate::{BitstreamChunk, Encoder, LowLatencyConfig};

#[cfg(feature = "x264-fallback")]
use crate::warn_fallback_in_use;
#[cfg(feature = "x264-fallback")]
use ezpeek_core::VideoCodec;

#[cfg(not(feature = "x264-fallback"))]
#[derive(Default)]
pub struct X264EncoderNoHw {
    cfg: LowLatencyConfig,
}

#[cfg(not(feature = "x264-fallback"))]
pub use X264EncoderNoHw as X264Encoder;

#[cfg(feature = "x264-fallback")]
pub struct X264Encoder {
    encoder: Option<x264::Encoder>,
    width: u32,
    height: u32,
    fps: u32,
    cfg: LowLatencyConfig,
    seq: u64,
    warned: bool,
}

#[cfg(not(feature = "x264-fallback"))]
impl X264EncoderNoHw {
    pub fn new(_width: u32, _height: u32, _fps: u32) -> Self {
        Self::default()
    }
}

#[cfg(feature = "x264-fallback")]
impl X264Encoder {
    pub fn new(width: u32, height: u32, fps: u32) -> Self {
        Self {
            encoder: None,
            width,
            height,
            fps,
            cfg: LowLatencyConfig::default(),
            seq: 0,
            warned: false,
        }
    }
}

#[cfg(feature = "x264-fallback")]
impl Default for X264Encoder {
    fn default() -> Self {
        Self::new(1280, 720, 30)
    }
}

#[cfg(feature = "x264-fallback")]
fn frame_yuv420(frame: &GpuFrame, width: u32, height: u32) -> Result<Vec<u8>, EzpeekError> {
    use ezpeek_core::FrameHandle;
    let y_len = (width as usize) * (height as usize);
    let total = y_len + y_len / 2;
    match frame.handle {
        FrameHandle::CpuNv12(ptr, len) => {
            if len < total || ptr.is_null() {
                return Err(EzpeekError::Encode("x264: CpuNv12 buffer too small".into()));
            }
            let src = unsafe { std::slice::from_raw_parts(ptr, total) };
            let mut buf = vec![0u8; total + y_len / 2];
            buf[..y_len].copy_from_slice(&src[..y_len]);
            let uv_src = &src[y_len..total];
            let (dst_u, dst_v) = buf[y_len..].split_at_mut(y_len / 4);
            for (i, quad) in uv_src.chunks_exact(4).enumerate() {
                if i < dst_u.len() {
                    dst_u[i] = quad[0];
                    dst_v[i] = quad[2];
                }
            }
            Ok(buf)
        }
        _ => {
            let uv = y_len / 4;
            let mut buf = vec![0x80u8; y_len + 2 * uv];
            buf[..y_len].fill(0x10);
            Ok(buf)
        }
    }
}

impl Encoder for X264Encoder {
    fn encode(&mut self, frame: &GpuFrame) -> Result<BitstreamChunk, EzpeekError> {
        #[cfg(not(feature = "x264-fallback"))]
        {
            let _ = frame;
            Err(EzpeekError::Unsupported(
                "x264: rebuild with --features x264-fallback",
            ))
        }
        #[cfg(feature = "x264-fallback")]
        {
            if !self.warned {
                warn_fallback_in_use("x264 H.264");
                self.warned = true;
            }
            if self.encoder.is_none() {
                let setup =
                    x264::Setup::preset(x264::Preset::Ultrafast, x264::Tune::None, false, true)
                        .fps(self.fps, 1)
                        .bitrate((self.cfg.bitrate_bps / 1000) as i32)
                        .max_keyframe_interval(self.cfg.gop_len as i32)
                        .min_keyframe_interval(self.cfg.gop_len as i32)
                        .scenecut_threshold(0);
                let enc = setup
                    .build(
                        x264::Colorspace::I420,
                        self.width as i32,
                        self.height as i32,
                    )
                    .map_err(|_| EzpeekError::Encode("x264 open failed".into()))?;
                self.encoder = Some(enc);
            }
            let enc = self
                .encoder
                .as_mut()
                .ok_or_else(|| EzpeekError::Encode("x264 not open".into()))?;
            let yuv = frame_yuv420(frame, self.width, self.height)?;
            let (w, h) = (self.width as i32, self.height as i32);
            let y_len = (w as usize) * (h as usize);
            let uv_len = y_len / 4;
            let image = x264::Image::new(
                x264::Colorspace::I420,
                w,
                h,
                &[
                    x264::Plane {
                        stride: w,
                        data: &yuv[..y_len],
                    },
                    x264::Plane {
                        stride: w / 2,
                        data: &yuv[y_len..y_len + uv_len],
                    },
                    x264::Plane {
                        stride: w / 2,
                        data: &yuv[y_len + uv_len..],
                    },
                ],
            );
            let (data, _) = enc
                .encode(frame.timestamp_ns as i64, image)
                .map_err(|_| EzpeekError::Encode("x264 encode failed".into()))?;
            let seq = self.seq;
            self.seq += 1;
            Ok(BitstreamChunk {
                codec: VideoCodec::H264,
                data: data.entirety().to_vec(),
                timestamp_ns: frame.timestamp_ns,
                seq,
                is_keyframe: seq.is_multiple_of(self.cfg.gop_len as u64),
            })
        }
    }

    fn configure_low_latency(&mut self, cfg: &LowLatencyConfig) -> Result<(), EzpeekError> {
        self.cfg = *cfg;
        #[cfg(feature = "x264-fallback")]
        {
            self.encoder = None;
        }
        Ok(())
    }
}
