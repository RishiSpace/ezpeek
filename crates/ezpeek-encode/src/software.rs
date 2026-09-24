//! Software codec fallback (compatibility path only).
//! Gated behind `software-fallback`; used only when no hardware
//! encoder exists. Callers must surface fallback in diagnostics.
//!
//! The `x264-fallback` feature enables the real x264 software H.264
//! encoder backend (zerolatency preset, zero B-frames). Without it,
//! only the availability stubs compile.

use ezpeek_core::EzpeekError;

pub use crate::warn_fallback_in_use;

pub fn check_available() -> Result<(), EzpeekError> {
    Err(EzpeekError::Unsupported(
        "software fallback: no backend enabled (enable x264-fallback)",
    ))
}
