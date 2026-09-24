//! NVIDIA NVDEC hardware decoder backend.
//! Target: Windows/Linux, NVIDIA GPUs via Video Codec SDK 12.x.
//! Status: stub — Milestone 1 target.

use ezpeek_core::{DecodedSurface, EzpeekError};

use crate::BitstreamChunk;

pub struct NvdecDecoder;

impl crate::Decoder for NvdecDecoder {
    fn decode(&mut self, _chunk: &BitstreamChunk) -> Result<DecodedSurface, EzpeekError> {
        Err(EzpeekError::Unsupported("nvdec: not yet implemented"))
    }
}
