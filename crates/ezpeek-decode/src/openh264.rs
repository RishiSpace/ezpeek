//! Software H.264 decoder backend (openh264, fallback/verify path).
//! Used when no hardware decoder exists, and to verify NVENC/x264
//! output in the loopback pipeline. Feature: `openh264-fallback`.

use ezpeek_core::{DecodedSurface, EzpeekError};

#[cfg(feature = "openh264-fallback")]
use ezpeek_core::FrameHandle;

use crate::BitstreamChunk;

pub struct OpenH264Decoder {
    #[cfg(feature = "openh264-fallback")]
    dec: Option<openh264::decoder::Decoder>,
    #[cfg(feature = "openh264-fallback")]
    owned: Vec<Vec<u8>>,
    #[allow(dead_code)]
    width: u32,
    #[allow(dead_code)]
    height: u32,
}

impl OpenH264Decoder {
    pub fn new(width: u32, height: u32) -> Self {
        Self {
            #[cfg(feature = "openh264-fallback")]
            dec: None,
            #[cfg(feature = "openh264-fallback")]
            owned: Vec::new(),
            width,
            height,
        }
    }

    #[cfg(feature = "openh264-fallback")]
    pub fn last_nv12(&self) -> Option<&[u8]> {
        self.owned.last().map(|v| v.as_slice())
    }
}

impl crate::Decoder for OpenH264Decoder {
    fn decode(&mut self, chunk: &BitstreamChunk) -> Result<DecodedSurface, EzpeekError> {
        #[cfg(not(feature = "openh264-fallback"))]
        {
            let _ = chunk;
            Err(EzpeekError::Unsupported(
                "openh264: rebuild with --features openh264-fallback",
            ))
        }
        #[cfg(feature = "openh264-fallback")]
        {
            if self.dec.is_none() {
                let dec = openh264::decoder::Decoder::new()
                    .map_err(|e| EzpeekError::Decode(format!("openh264 open: {e:?}")))?;
                self.dec = Some(dec);
            }
            let dec = self
                .dec
                .as_mut()
                .ok_or_else(|| EzpeekError::Decode("openh264 not open".into()))?;
            let yuv = dec
                .decode(&chunk.data)
                .map_err(|e| EzpeekError::Decode(format!("openh264 decode: {e:?}")))?;
            match yuv {
                Some(frame) => {
                    use openh264::formats::YUVSource;
                    let (w, h) = frame.dimensions();
                    let (sy, su, _sv) = frame.strides();
                    let y = frame.y();
                    let u = frame.u();
                    let v = frame.v();
                    let mut nv12 = vec![0u8; w * h * 3 / 2];
                    for row in 0..h {
                        nv12[row * w..row * w + w].copy_from_slice(&y[row * sy..row * sy + w]);
                    }
                    let uv = &mut nv12[w * h..];
                    for row in 0..h / 2 {
                        for col in 0..w / 2 {
                            uv[row * w + col * 2] = u[row * su + col];
                            uv[row * w + col * 2 + 1] = v[row * su + col];
                        }
                    }
                    self.owned.push(nv12);
                    if self.owned.len() > 4 {
                        self.owned.remove(0);
                    }
                    let last = self.owned.last().unwrap();
                    Ok(DecodedSurface {
                        handle: FrameHandle::CpuNv12(last.as_ptr(), last.len()),
                        width: w as u32,
                        height: h as u32,
                        timestamp_ns: chunk.timestamp_ns,
                    })
                }
                None => Err(EzpeekError::Decode("openh264: need more data".into())),
            }
        }
    }
}
