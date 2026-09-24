//! AMD AMF hardware encoder backend.
//! Target: Windows, AMD GPUs via Advanced Media Framework 1.4.x
//! (gpuopen.com/advanced-media-framework).
//! Status: stub — returns `Unsupported` until a later milestone implements it.

use ezpeek_core::{EzpeekError, GpuFrame};

use crate::{BitstreamChunk, Encoder, LowLatencyConfig};

pub struct AmfEncoder;

impl Encoder for AmfEncoder {
    fn encode(&mut self, _frame: &GpuFrame) -> Result<BitstreamChunk, EzpeekError> {
        Err(EzpeekError::Unsupported("amf: not yet implemented"))
    }

    fn configure_low_latency(&mut self, _cfg: &LowLatencyConfig) -> Result<(), EzpeekError> {
        Err(EzpeekError::Unsupported("amf: not yet implemented"))
    }
}
