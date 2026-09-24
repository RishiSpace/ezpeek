//! Windows DXGI Desktop Duplication capture backend.
//! Target: Windows 10/11, DXGI `IDXGIOutputDuplication`.
//! Vendor SDK: microsoft/windows-rs (docs.rs/windows).
//! Status: stub — returns `Unsupported` until Milestone 1 implements it.

use crate::UnsupportedCapture;

pub fn create() -> UnsupportedCapture {
    UnsupportedCapture("dxgi: not yet implemented")
}
