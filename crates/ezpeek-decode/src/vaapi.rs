//! VAAPI hardware decoder backend (Linux Intel/AMD).
//! Target: Linux, libva 2.x. Status: stub (Milestone 6).

use ezpeek_core::{DecodedSurface, EzpeekError};

use crate::BitstreamChunk;

pub struct VaapiDecoder;

impl crate::Decoder for VaapiDecoder {
    fn decode(&mut self, _chunk: &BitstreamChunk) -> Result<DecodedSurface, EzpeekError> {
        Err(EzpeekError::Unsupported(
            "vaapi decode: not yet implemented",
        ))
    }
}
