//! Software decode fallback (dav1d/openh264).
//! Gated behind `software-fallback`; diagnostics must surface use.

use ezpeek_core::EzpeekError;

pub fn check_available() -> Result<(), EzpeekError> {
    Err(EzpeekError::Unsupported(
        "software fallback: dav1d/openh264 not yet wired",
    ))
}
