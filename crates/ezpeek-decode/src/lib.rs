use ezpeek_core::{DecodedSurface, EzpeekError};

pub use ezpeek_encode::{low_latency_cbr, BitstreamChunk, EncoderBackend, LowLatencyConfig};

pub mod amf;
pub mod nvdec;
pub mod openh264;
pub mod vaapi;
pub mod videotoolbox;

#[cfg(feature = "software-fallback")]
pub mod software;

pub trait Decoder {
    fn decode(&mut self, chunk: &BitstreamChunk) -> Result<DecodedSurface, EzpeekError>;
}
