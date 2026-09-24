//! Hardware loopback test: capture → encode → decode → present on one machine.
//! Target: Windows 10/11, DXGI capture + NVENC H.264 + NVDEC.
//! Vendor SDK: Video Codec SDK 12.x, microsoft/windows-rs.
//! Run: `cargo test -p ezpeek-integration --features hw-tests -- --ignored`
//! Requires: NVIDIA GPU with NVENC/NVDEC, Windows 10/11.

#[test]
#[ignore]
#[cfg(not(feature = "hw-tests"))]
fn hw_loopback_glass_to_glass() {
    panic!("rebuild with --features hw-tests");
}

#[test]
#[ignore]
#[cfg(feature = "hw-tests")]
fn hw_loopback_glass_to_glass() {
    unimplemented!("Milestone 1: wire DXGI → NVENC → loopback → NVDEC → present");
}
