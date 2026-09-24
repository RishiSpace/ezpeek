//! Software presenter: YUV → RGB window via minifb.
//! Linux-first path until the EGL/Vulkan zero-copy presenter lands.
//! Decoded YUV pixels come from the paired openh264 decoder output
//! passed alongside the surface. Feature: `software-present`.

use ezpeek_core::{DecodedSurface, EzpeekError, FrameHandle};

use crate::Presenter;

pub struct SoftwarePresenter {
    window: Option<minifb::Window>,
    width: usize,
    height: usize,
}

impl SoftwarePresenter {
    pub fn new(width: usize, height: usize) -> Result<Self, EzpeekError> {
        Ok(Self {
            window: None,
            width,
            height,
        })
    }

    fn ensure_window(&mut self) -> Result<(), EzpeekError> {
        if self.window.is_some() {
            return Ok(());
        }
        let opts = minifb::WindowOptions {
            resize: true,
            ..Default::default()
        };
        let window = minifb::Window::new("ezpeek-viewer", self.width, self.height, opts)
            .map_err(|e| EzpeekError::Present(format!("window: {e:?}")))?;
        self.window = Some(window);
        Ok(())
    }
}

impl Presenter for SoftwarePresenter {
    fn present(&mut self, surface: &DecodedSurface) -> Result<(), EzpeekError> {
        self.ensure_window()?;
        let window = self
            .window
            .as_mut()
            .ok_or_else(|| EzpeekError::Present("no window".into()))?;
        let (w, h) = (surface.width as usize, surface.height as usize);
        let rgb: Vec<u32> = match surface.handle {
            FrameHandle::CpuNv12(ptr, len) if !ptr.is_null() && len >= w * h * 3 / 2 => {
                let src = unsafe { std::slice::from_raw_parts(ptr, w * h * 3 / 2) };
                nv12_to_xrgb(src, w, h)
            }
            _ => vec![0x00101010; w * h],
        };
        window
            .update_with_buffer(&rgb, w, h)
            .map_err(|e| EzpeekError::Present(format!("present: {e:?}")))?;
        Ok(())
    }
}

fn nv12_to_xrgb(src: &[u8], w: usize, h: usize) -> Vec<u32> {
    let y_len = w * h;
    let mut out = vec![0u32; y_len];
    for y in 0..h {
        for x in 0..w {
            let yi = y * w + x;
            let yv = src[yi] as i32;
            let uvi = y_len + (y / 2) * w + (x / 2) * 2;
            let u = src[uvi] as i32 - 128;
            let v = src[uvi + 1] as i32 - 128;
            let c = yv - 16;
            let r = ((298 * c + 409 * v + 128) >> 8).clamp(0, 255) as u32;
            let g = ((298 * c - 100 * u - 208 * v + 128) >> 8).clamp(0, 255) as u32;
            let b = ((298 * c + 516 * u + 128) >> 8).clamp(0, 255) as u32;
            out[yi] = (r << 16) | (g << 8) | b;
        }
    }
    out
}
