use ezpeek_core::{now_ns, FrameHandle, GpuFrame, PixelFormat};
use ezpeek_encode::{vaapi::VaapiEncoder, Encoder};
fn main() {
    let mut enc = VaapiEncoder::new(320, 240, 30);
    let w = 320usize;
    let h = 240usize;
    let buf = vec![0x80u8; w * h * 3 / 2];
    let f = GpuFrame {
        handle: FrameHandle::CpuNv12(buf.as_ptr(), buf.len()),
        width: 320,
        height: 240,
        format: PixelFormat::Nv12,
        timestamp_ns: now_ns(),
        seq: 0,
    };
    // keep buf alive; leak for probe simplicity
    std::mem::forget(buf);
    match enc.encode(&f) {
        Ok(c) => {
            println!("chunk len={} key={}", c.data.len(), c.is_keyframe);
            println!(
                "hex: {}",
                c.data
                    .iter()
                    .take(48)
                    .map(|b| format!("{b:02x}"))
                    .collect::<String>()
            );
            // list all start codes + nal types
            let mut i = 0;
            while i + 4 <= c.data.len() {
                if c.data[i] == 0 && c.data[i + 1] == 0 && c.data[i + 2] == 0 && c.data[i + 3] == 1
                {
                    if i + 4 < c.data.len() {
                        println!(
                            "sc@{} nal_hdr={:02x} type={}",
                            i,
                            c.data[i + 4],
                            c.data[i + 4] & 0x1f
                        );
                    }
                    i += 4;
                } else {
                    i += 1;
                }
            }
            std::fs::write("/tmp/vraw.h264", &c.data).unwrap();
        }
        Err(e) => println!("err: {e}"),
    }
}
