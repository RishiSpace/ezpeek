//! AMD AMF hardware decoder backend.
//! Target: Windows, AMD GPUs via Advanced Media Framework 1.4.x.
//! Status: stub.

use ezpeek_core::{DecodedSurface, EzpeekError};

use crate::BitstreamChunk;

pub struct AmfDecoder;

impl crate::Decoder for AmfDecoder {
    fn decode(&mut self, _chunk: &BitstreamChunk) -> Result<DecodedSurface, EzpeekError> {
        Err(EzpeekError::Unsupported("amf decode: not yet implemented"))
    }
}
