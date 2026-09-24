//! macOS ScreenCaptureKit capture backend.
//! Target: macOS 13+, `SCStream` with IOSurface-backed `CVPixelBuffer`.
//! Vendor SDK: objc2-screen-capture-kit (docs.rs/objc2).
//! Status: stub — returns `Unsupported` until Milestone 7 implements it.

use crate::UnsupportedCapture;

pub fn create() -> UnsupportedCapture {
    UnsupportedCapture("screencapturekit: not yet implemented")
}
