use ezpeek_core::{EzpeekError, GpuFrame};

pub mod dxgi;
pub mod pipewire;
pub mod screencapturekit;

#[cfg(feature = "portal")]
pub mod portal;

pub trait CaptureSource {
    fn next_frame(&mut self) -> Result<GpuFrame, EzpeekError>;
}

pub struct UnsupportedCapture(&'static str);

impl CaptureSource for UnsupportedCapture {
    fn next_frame(&mut self) -> Result<GpuFrame, EzpeekError> {
        Err(EzpeekError::Unsupported(self.0))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CaptureBackend {
    PipeWire,
    Dxgi,
    ScreenCaptureKit,
}

impl CaptureBackend {
    pub fn platform_default() -> Self {
        #[cfg(target_os = "linux")]
        return Self::PipeWire;
        #[cfg(target_os = "windows")]
        return Self::Dxgi;
        #[cfg(target_os = "macos")]
        return Self::ScreenCaptureKit;
        #[cfg(not(any(target_os = "linux", target_os = "windows", target_os = "macos")))]
        return Self::PipeWire;
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::PipeWire => "pipewire",
            Self::Dxgi => "dxgi",
            Self::ScreenCaptureKit => "screencapturekit",
        }
    }
}
