use serde::{Deserialize, Serialize};

use crate::format::{PixelFormat, VideoCodec};

#[derive(Debug, Clone)]
pub enum FrameHandle {
    D3D11Texture(*mut core::ffi::c_void),
    DmaBuf(DmaBufFd),
    IOSurface(*mut core::ffi::c_void),
    Synthetic(u64),
    CpuNv12(*const u8, usize),
}

/// Owned DMA-BUF fd wrapper: exactly one owner closes exactly once.
/// Construct only via `DmaBufFd::adopt` on a freshly-dup'd fd, or
/// `DmaBufFd::take` from an `OwnedFd`. Never `from_raw_fd` on a
/// borrowed or already-owned fd.
#[derive(Debug)]
pub struct DmaBufFd {
    fd: Option<std::os::fd::OwnedFd>,
}

impl DmaBufFd {
    pub fn adopt(fd: std::os::fd::OwnedFd) -> Self {
        Self { fd: Some(fd) }
    }

    pub fn raw(&self) -> i32 {
        use std::os::fd::AsRawFd;
        self.fd.as_ref().map(|f| f.as_raw_fd()).unwrap_or(-1)
    }

    pub fn take(mut self) -> std::os::fd::OwnedFd {
        self.fd.take().expect("DmaBufFd already taken")
    }

    pub fn is_valid(&self) -> bool {
        self.fd.is_some()
    }
}

impl Clone for DmaBufFd {
    fn clone(&self) -> Self {
        use std::os::fd::{AsRawFd, FromRawFd};
        match &self.fd {
            Some(f) => {
                let duped = libc_dup(f.as_raw_fd());
                assert!(duped >= 0, "DmaBufFd clone: dup failed");
                Self {
                    fd: Some(unsafe { std::os::fd::OwnedFd::from_raw_fd(duped) }),
                }
            }
            None => Self { fd: None },
        }
    }
}

impl Drop for DmaBufFd {
    fn drop(&mut self) {
        drop(self.fd.take());
    }
}

#[cfg(unix)]
fn libc_dup(fd: std::os::raw::c_int) -> std::os::raw::c_int {
    unsafe { libc::dup(fd) }
}

#[cfg(not(unix))]
fn libc_dup(_fd: i32) -> i32 {
    -1
}

unsafe impl Send for FrameHandle {}
unsafe impl Sync for FrameHandle {}

#[derive(Debug, Clone)]
pub struct GpuFrame {
    pub handle: FrameHandle,
    pub width: u32,
    pub height: u32,
    pub format: PixelFormat,
    pub timestamp_ns: u64,
    pub seq: u64,
}

#[derive(Debug, Clone)]
pub struct DecodedSurface {
    pub handle: FrameHandle,
    pub width: u32,
    pub height: u32,
    pub timestamp_ns: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Capability {
    pub encode_hw_h264: bool,
    pub decode_hw_h264: bool,
    pub encode_hw_av1: bool,
    pub decode_hw_av1: bool,
    pub max_width: u32,
    pub max_height: u32,
    pub max_fps: u32,
    pub bandwidth_bps: u64,
}

pub fn select_codec(host: &Capability, viewer: &Capability) -> VideoCodec {
    let av1_hw = host.encode_hw_av1 && viewer.decode_hw_av1;
    if av1_hw && bandwidth_supports_av1(host, viewer) {
        VideoCodec::Av1
    } else {
        VideoCodec::H264
    }
}

fn bandwidth_supports_av1(host: &Capability, viewer: &Capability) -> bool {
    let need_bps = estimated_av1_bps(host.max_width, host.max_height, host.max_fps);
    host.bandwidth_bps >= need_bps && viewer.bandwidth_bps >= need_bps
}

fn estimated_av1_bps(w: u32, h: u32, fps: u32) -> u64 {
    (w as u64) * (h as u64) * (fps as u64) / 200
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cap(av1: bool) -> Capability {
        Capability {
            encode_hw_h264: true,
            decode_hw_h264: true,
            encode_hw_av1: av1,
            decode_hw_av1: av1,
            max_width: 1920,
            max_height: 1080,
            max_fps: 60,
            bandwidth_bps: 50_000_000,
        }
    }

    #[test]
    fn both_av1_with_bandwidth_selects_av1() {
        assert_eq!(select_codec(&cap(true), &cap(true)), VideoCodec::Av1);
    }

    #[test]
    fn missing_av1_selects_h264() {
        assert_eq!(select_codec(&cap(false), &cap(true)), VideoCodec::H264);
        assert_eq!(select_codec(&cap(true), &cap(false)), VideoCodec::H264);
    }

    #[test]
    fn low_bandwidth_selects_h264() {
        let mut low = cap(true);
        low.bandwidth_bps = 1000;
        assert_eq!(select_codec(&low, &cap(true)), VideoCodec::H264);
        assert_eq!(select_codec(&cap(true), &low), VideoCodec::H264);
    }

    #[test]
    fn h264_baseline_always_available() {
        let no_hw = Capability {
            encode_hw_h264: true,
            decode_hw_h264: true,
            encode_hw_av1: false,
            decode_hw_av1: false,
            max_width: 1280,
            max_height: 720,
            max_fps: 30,
            bandwidth_bps: 5_000_000,
        };
        assert_eq!(select_codec(&no_hw, &no_hw), VideoCodec::H264);
    }
}
