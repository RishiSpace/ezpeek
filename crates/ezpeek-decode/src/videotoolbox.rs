//! Apple VideoToolbox hardware decoder backend.
//! Target: macOS 13+, `VTDecompressionSession`. Status: stub (Milestone 7).

use ezpeek_core::{DecodedSurface, EzpeekError};

use crate::BitstreamChunk;

pub struct VideoToolboxDecoder;

impl crate::Decoder for VideoToolboxDecoder {
    fn decode(&mut self, _chunk: &BitstreamChunk) -> Result<DecodedSurface, EzpeekError> {
        Err(EzpeekError::Unsupported(
            "videotoolbox decode: not yet implemented",
        ))
    }
}
