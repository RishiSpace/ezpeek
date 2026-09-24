//! Apple VideoToolbox hardware encoder backend.
//! Target: macOS 13+, `VTCompressionSession` (developer.apple.com).
//! Status: stub — returns `Unsupported` until Milestone 7 implements it.

use ezpeek_core::{EzpeekError, GpuFrame};

use crate::{BitstreamChunk, Encoder, LowLatencyConfig};

pub struct VideoToolboxEncoder;

impl Encoder for VideoToolboxEncoder {
    fn encode(&mut self, _frame: &GpuFrame) -> Result<BitstreamChunk, EzpeekError> {
        Err(EzpeekError::Unsupported(
            "videotoolbox: not yet implemented",
        ))
    }

    fn configure_low_latency(&mut self, _cfg: &LowLatencyConfig) -> Result<(), EzpeekError> {
        Err(EzpeekError::Unsupported(
            "videotoolbox: not yet implemented",
        ))
    }
}
