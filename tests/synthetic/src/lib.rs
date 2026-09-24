use ezpeek_capture::CaptureSource;
use ezpeek_core::{now_ns, FrameHandle, GpuFrame, PixelFormat};

pub struct SyntheticCapture {
    width: u32,
    height: u32,
    seq: u64,
    x: u32,
    buf: Vec<u8>,
}

impl SyntheticCapture {
    pub fn new(width: u32, height: u32) -> Self {
        let y_len = width as usize * height as usize;
        Self {
            width,
            height,
            seq: 0,
            x: 0,
            buf: vec![0x80u8; y_len + y_len / 2],
        }
    }

    fn draw(&mut self) {
        let (w, h) = (self.width as usize, self.height as usize);
        let y_len = w * h;
        let xoff = self.x as usize;
        let y_row: Vec<u8> = (0..w)
            .map(|x| {
                let bar = ((x + xoff) / 32) % 2;
                if bar == 0 {
                    (16 + (x * 219 / w.max(1)) as u8).max(16)
                } else {
                    128
                }
            })
            .collect();
        for (y, row) in self.buf[..y_len].chunks_exact_mut(w).enumerate() {
            if ((xoff / 32) + y / 32).is_multiple_of(2) {
                row.copy_from_slice(&y_row);
            } else {
                let v = (16 + (y * 219 / h.max(1)) as u8).max(16);
                row.fill(v);
            }
        }
        for px in self.buf[y_len..].iter_mut() {
            *px = 0x80;
        }
    }
}

impl CaptureSource for SyntheticCapture {
    fn next_frame(&mut self) -> Result<GpuFrame, ezpeek_core::EzpeekError> {
        self.x = (self.x + 8) % self.width.max(1);
        self.draw();
        let frame = GpuFrame {
            handle: FrameHandle::CpuNv12(self.buf.as_ptr(), self.buf.len()),
            width: self.width,
            height: self.height,
            format: PixelFormat::Nv12,
            timestamp_ns: now_ns(),
            seq: self.seq,
        };
        self.seq += 1;
        Ok(frame)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn synthetic_moves_and_timestamps_increase() {
        let mut cap = SyntheticCapture::new(640, 480);
        let a = cap.next_frame().unwrap();
        let b = cap.next_frame().unwrap();
        assert!(b.seq > a.seq);
        assert!(b.timestamp_ns >= a.timestamp_ns);
    }

    #[test]
    fn synthetic_carries_cpu_pixels() {
        let mut cap = SyntheticCapture::new(64, 48);
        let f = cap.next_frame().unwrap();
        match f.handle {
            FrameHandle::CpuNv12(ptr, len) => {
                assert_eq!(len, 64 * 48 * 3 / 2);
                assert!(!ptr.is_null());
            }
            _ => panic!("expected CpuNv12"),
        }
    }
}
