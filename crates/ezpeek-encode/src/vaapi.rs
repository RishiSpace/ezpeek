//! VAAPI hardware encoder backend (Linux Intel/AMD).
//! Target: Linux, libva 2.x via the `libva` crate (libva 2.24 present).
//! (freedesktop.org/wiki/Software/vaapi).
//!
//! Status: INVESTIGATING — output structure TBD. The AMD radeonsi VCN
//! encoder accepts the full vaBeginPicture/vaRenderPicture/vaEndPicture
//! sequence (status 0) but emitted units carry NAL header 0x00
//! (forbidden_zero, type 0 = unspecified) instead of slice NALs (type 1/5).
//! Packed SPS/PPS are silently dropped despite 0x1f support reported.
//! Software SPS/PPS prepend was tried and produces a structured stream
//! (SPS+PPS+units) that still fails ffmpeg decode — the slice payload
//! itself is not a valid H.264 slice. ffmpeg h264_vaapi fails identically
//! ("No usable encoding entrypoint"). NVENC is the working HW path;
//! x264 is the fallback. Next step: capture a known-good AMD VCN parameter
//! set (e.g. from gstreamer vaapih264enc or mesa gears) and diff.
//!
//! Upload path when enabled: system-memory NV12 via VAImage;
//! DMA-BUF zero-copy import is the follow-up. Feature: `vaapi`.

use ezpeek_core::{EzpeekError, GpuFrame};

#[cfg(feature = "vaapi")]
use ezpeek_core::VideoCodec;

use crate::{BitstreamChunk, Encoder, LowLatencyConfig};

#[cfg(feature = "vaapi")]
use std::rc::Rc;

pub struct VaapiEncoder {
    #[cfg(feature = "vaapi")]
    state: Option<VaapiState>,
    #[cfg(feature = "vaapi")]
    width: u32,
    #[cfg(feature = "vaapi")]
    height: u32,
    #[cfg(feature = "vaapi")]
    fps: u32,
    cfg: LowLatencyConfig,
    #[cfg(feature = "vaapi")]
    seq: u64,
    #[cfg(feature = "vaapi")]
    frame_num: u16,
}

#[cfg(feature = "vaapi")]
struct VaapiState {
    _display: std::sync::Arc<libva::Display>,
    context: Rc<libva::Context>,
    surface: libva::Surface<()>,
    coded_buf: libva::EncCodedBuffer,
}

impl VaapiEncoder {
    pub fn new(_width: u32, _height: u32, _fps: u32) -> Self {
        Self {
            #[cfg(feature = "vaapi")]
            state: None,
            #[cfg(feature = "vaapi")]
            width: _width,
            #[cfg(feature = "vaapi")]
            height: _height,
            #[cfg(feature = "vaapi")]
            fps: _fps,
            cfg: LowLatencyConfig::default(),
            #[cfg(feature = "vaapi")]
            seq: 0,
            #[cfg(feature = "vaapi")]
            frame_num: 0,
        }
    }

    #[cfg(feature = "vaapi")]
    fn open_display() -> Result<std::sync::Arc<libva::Display>, EzpeekError> {
        if let Ok(node) = std::env::var("VAAPI_NODE") {
            if std::env::var("LIBVA_DRIVER_NAME").as_deref() == Ok("nvidia") {
                unsafe { std::env::remove_var("LIBVA_DRIVER_NAME") };
            }
            return libva::Display::open_drm_display(&node)
                .map_err(|e| EzpeekError::Encode(format!("vaapi open {node}: {e:?}")));
        }
        let mut nvidia_fallback = None;
        for node in ["/dev/dri/renderD129", "/dev/dri/renderD128"] {
            match libva::Display::open_drm_display(node) {
                Ok(d) => match d.query_vendor_string() {
                    Ok(vendor) if !vendor.contains("NVDEC") => return Ok(d),
                    Ok(_) => {
                        if nvidia_fallback.is_none() {
                            nvidia_fallback = Some(d);
                        }
                    }
                    Err(_) => return Ok(d),
                },
                Err(_) => continue,
            }
        }
        nvidia_fallback
            .or_else(libva::Display::open)
            .ok_or_else(|| EzpeekError::Encode("vaapi: no display".into()))
    }

    #[cfg(feature = "vaapi")]
    fn ensure_open(&mut self) -> Result<(), EzpeekError> {
        if self.state.is_some() {
            return Ok(());
        }
        let display = Self::open_display()?;
        for wanted in [
            libva::VAProfile::VAProfileH264High,
            libva::VAProfile::VAProfileH264Main,
            libva::VAProfile::VAProfileH264ConstrainedBaseline,
        ] {
            let profiles = display
                .query_config_profiles()
                .map_err(|e| EzpeekError::Encode(format!("vaapi profiles: {e:?}")))?;
            if !profiles.contains(&wanted) {
                continue;
            }
            let entrypoints = display
                .query_config_entrypoints(wanted)
                .map_err(|e| EzpeekError::Encode(format!("vaapi entrypoints: {e:?}")))?;
            if !entrypoints.contains(&libva::VAEntrypoint::VAEntrypointEncSlice) {
                continue;
            }
            let mut packed_attrs = vec![libva::VAConfigAttrib {
                type_: libva::VAConfigAttribType::VAConfigAttribEncPackedHeaders,
                value: 0,
            }];
            const PACKED_SEQ: u32 = 0x00000001;
            const PACKED_PIC: u32 = 0x00000002;
            const PACKED_SLICE: u32 = 0x00000004;
            let packed_supported = display
                .get_config_attributes(
                    wanted,
                    libva::VAEntrypoint::VAEntrypointEncSlice,
                    &mut packed_attrs,
                )
                .is_ok()
                && packed_attrs[0].value & (PACKED_SEQ | PACKED_PIC | PACKED_SLICE)
                    == (PACKED_SEQ | PACKED_PIC | PACKED_SLICE);
            if !packed_supported {
                continue;
            }
            let config = display
                .create_config(vec![], wanted, libva::VAEntrypoint::VAEntrypointEncSlice)
                .map_err(|e| EzpeekError::Encode(format!("vaapi config: {e:?}")))?;
            let format = libva::VA_RT_FORMAT_YUV420;
            let fourcc = libva::VA_FOURCC_NV12;
            let mut surfaces: Vec<libva::Surface<()>> = display
                .create_surfaces(
                    format,
                    Some(fourcc),
                    self.width,
                    self.height,
                    None,
                    vec![()],
                )
                .map_err(|e| EzpeekError::Encode(format!("vaapi surface: {e:?}")))?;
            let surface = surfaces
                .pop()
                .ok_or_else(|| EzpeekError::Encode("vaapi: no surface".into()))?;
            let context = display
                .create_context::<()>(&config, self.width, self.height, None, true)
                .map_err(|e| EzpeekError::Encode(format!("vaapi context: {e:?}")))?;
            let coded_buf = context
                .create_enc_coded(self.width as usize * self.height as usize * 4)
                .map_err(|e| EzpeekError::Encode(format!("vaapi coded buf: {e:?}")))?;
            self.state = Some(VaapiState {
                _display: display,
                context,
                surface,
                coded_buf,
            });
            return Ok(());
        }
        Err(EzpeekError::Encode(
            "vaapi: no H264 EncSlice profile on this display".into(),
        ))
    }
    #[cfg(feature = "vaapi")]
    fn upload_nv12_blank(&mut self) -> Result<(), EzpeekError> {
        let (w, h) = (self.width, self.height);
        let st = self
            .state
            .as_mut()
            .ok_or_else(|| EzpeekError::Encode("vaapi not open".into()))?;
        let formats = st
            ._display
            .query_image_formats()
            .map_err(|e| EzpeekError::Encode(format!("vaapi img fmts: {e:?}")))?;
        let nv12 = formats
            .iter()
            .find(|f| f.fourcc == libva::VA_FOURCC_NV12)
            .ok_or_else(|| EzpeekError::Encode("vaapi: no NV12 image format".into()))?;
        let mut image = libva::Image::create_from(&st.surface, *nv12, (w, h), (w, h))
            .map_err(|e| EzpeekError::Encode(format!("vaapi image: {e:?}")))?;
        {
            let data: &mut [u8] = image.as_mut();
            let (w, h) = (w as usize, h as usize);
            let y_len = w * h;
            if data.len() < y_len + y_len / 2 {
                return Err(EzpeekError::Encode("vaapi: image too small".into()));
            }
            data[..y_len].fill(0x10);
            data[y_len..y_len + y_len / 2].fill(0x80);
        }
        Ok(())
    }

    #[cfg(feature = "vaapi")]
    fn encode_one(&mut self, frame: &GpuFrame) -> Result<BitstreamChunk, EzpeekError> {
        use libva::{BufferType, Picture};
        fn va<T>(r: Result<T, libva::VaError>, what: &str) -> Result<T, EzpeekError> {
            r.map_err(|e| EzpeekError::Encode(format!("vaapi {what}: status={}", e.va_status())))
        }
        self.upload_nv12_blank()?;
        let (mb_w, mb_h, frame_num, gop_len, bitrate_kbps, fps) = (
            self.width.div_ceil(16) as u16,
            self.height.div_ceil(16) as u16,
            self.frame_num,
            self.cfg.gop_len,
            (self.cfg.bitrate_bps / 1000) as u32,
            self.fps,
        );
        let is_idr = self.seq.is_multiple_of(gop_len as u64);
        let seq_fields = libva::H264EncSeqFields::new(1, 1, 0, 0, 1, 0, 0, 0, 0);
        let seq_param = libva::EncSequenceParameterBufferH264::new(
            0,
            41,
            gop_len,
            gop_len,
            1,
            bitrate_kbps,
            1,
            mb_w,
            mb_h,
            &seq_fields,
            0,
            0,
            0,
            0,
            0,
            [0; 256],
            None,
            None,
            1,
            1,
            1,
            1,
            fps,
        );
        let st = self
            .state
            .as_mut()
            .ok_or_else(|| EzpeekError::Encode("vaapi not open".into()))?;
        let invalid = || libva::PictureH264::new(0xffffffff, 0, 0, 0, 0);
        let curr = libva::PictureH264::new(st.surface.id(), frame_num as u32, 0, 0, 0);
        let refs: [libva::PictureH264; 16] = std::array::from_fn(|_| invalid());
        let pic_fields =
            libva::H264EncPicFields::new(u32::from(is_idr), 1, 1, 0, 0, 0, 0, 0, 0, 0, 0);
        let pic_param = libva::EncPictureParameterBufferH264::new(
            curr,
            refs,
            st.coded_buf.id(),
            0,
            0,
            0,
            frame_num,
            26,
            0,
            0,
            0,
            0,
            &pic_fields,
        );
        let num_mb = mb_w as u32 * mb_h as u32;
        let slice_param = libva::EncSliceParameterBufferH264::new(
            0,
            num_mb,
            0,
            if is_idr { 2 } else { 0 },
            0,
            0,
            (frame_num.wrapping_mul(2)) % 2048,
            0,
            [0, 0],
            0,
            0,
            0,
            0,
            std::array::from_fn(|_| invalid()),
            std::array::from_fn(|_| invalid()),
            0,
            0,
            0,
            [0; 32],
            [0; 32],
            0,
            [[0; 2]; 32],
            [[0; 2]; 32],
            0,
            [0; 32],
            [0; 32],
            0,
            [[0; 2]; 32],
            [[0; 2]; 32],
            0,
            0,
            0,
            0,
            0,
        );
        let ctx = Rc::clone(&st.context);
        let mut picture = Picture::new(frame.timestamp_ns, ctx.clone(), &st.surface);
        let mut keepalive: Vec<libva::Buffer> = Vec::new();
        if is_idr {
            let sps: &[u8] = &[
                0x67, 0x42, 0x00, 0x1e, 0xac, 0x2b, 0x40, 0x50, 0x1e, 0xd0, 0x0f, 0x08, 0x84, 0x6a,
                0x20, 0x00, 0x00, 0x03, 0x00, 0x20, 0x00, 0x00, 0x03, 0x03, 0xc0, 0xf1, 0x42, 0x99,
                0x60,
            ];
            let pps: &[u8] = &[0x68, 0xce, 0x3c, 0x80];
            for (blob, what) in [(sps, "sps data"), (pps, "pps data")] {
                let owned = blob.to_vec();
                let leaked: &'static [u8] = Box::leak(owned.into_boxed_slice());
                let buf = va(
                    ctx.create_buffer_borrowed(libva::BorrowedBufferType::EncPackedHeaderData(
                        leaked,
                    )),
                    what,
                )?;
                keepalive.push(buf);
            }
            picture.add_buffer(va(
                ctx.create_buffer(BufferType::EncPackedHeaderParameter(
                    libva::EncPackedHeaderParameter::new(
                        libva::EncPackedHeaderType::Sequence,
                        (sps.len() * 8) as u32,
                        false,
                    ),
                )),
                "sps param",
            )?);
            picture.add_buffer(va(
                ctx.create_buffer(BufferType::EncPackedHeaderParameter(
                    libva::EncPackedHeaderParameter::new(
                        libva::EncPackedHeaderType::Picture,
                        (pps.len() * 8) as u32,
                        false,
                    ),
                )),
                "pps param",
            )?);
        }
        picture.add_buffer(va(
            ctx.create_buffer(BufferType::EncSequenceParameter(
                libva::EncSequenceParameter::H264(seq_param),
            )),
            "seq param",
        )?);
        picture.add_buffer(va(
            ctx.create_buffer(BufferType::EncPictureParameter(
                libva::EncPictureParameter::H264(pic_param),
            )),
            "pic param",
        )?);
        picture.add_buffer(va(
            ctx.create_buffer(BufferType::EncSliceParameter(
                libva::EncSliceParameter::H264(slice_param),
            )),
            "slice param",
        )?);
        let fr_buf = va(
            ctx.create_buffer(BufferType::EncMiscParameter(
                libva::EncMiscParameter::FrameRate(libva::EncMiscParameterFrameRate::new(
                    self.fps, 0,
                )),
            )),
            "misc framerate",
        )?;
        picture.add_buffer(fr_buf);
        let rc_buf = va(
            ctx.create_buffer(BufferType::EncMiscParameter(
                libva::EncMiscParameter::RateControl(libva::EncMiscParameterRateControl::new(
                    (self.cfg.bitrate_bps / 1000) as u32,
                    100,
                    0,
                    26,
                    0,
                    0,
                    libva::RcFlags::default(),
                    0,
                    51,
                    0,
                    0,
                )),
            )),
            "misc ratecontrol",
        )?;
        picture.add_buffer(rc_buf);
        let picture = va(picture.begin(), "begin")?;
        let picture = va(picture.render(), "render")?;
        let picture = va(picture.end(), "end")?;
        let _ = st.surface.sync();
        let mapped = va(libva::MappedCodedBuffer::new(&st.coded_buf), "map coded")?;
        let mut data = Vec::new();
        for seg in mapped.iter() {
            if seg.buf.is_empty() {
                continue;
            }
            data.extend_from_slice(seg.buf);
        }
        if is_idr {
            let mut framed = software_sps_pps(self.width, self.height);
            framed.extend_from_slice(&data);
            data = framed;
        }
        let seq = self.seq;
        self.seq += 1;
        self.frame_num = self.frame_num.wrapping_add(1);
        let _ = picture;
        Ok(BitstreamChunk {
            codec: VideoCodec::H264,
            data,
            timestamp_ns: frame.timestamp_ns,
            seq,
            is_keyframe: is_idr,
        })
    }
}

impl Encoder for VaapiEncoder {
    fn encode(&mut self, frame: &GpuFrame) -> Result<BitstreamChunk, EzpeekError> {
        #[cfg(not(feature = "vaapi"))]
        {
            let _ = frame;
            Err(EzpeekError::Unsupported(
                "vaapi: rebuild with --features vaapi",
            ))
        }
        #[cfg(feature = "vaapi")]
        {
            self.ensure_open()?;
            self.encode_one(frame)
        }
    }

    fn configure_low_latency(&mut self, cfg: &LowLatencyConfig) -> Result<(), EzpeekError> {
        self.cfg = *cfg;
        #[cfg(feature = "vaapi")]
        {
            self.state = None;
        }
        Ok(())
    }
}

#[cfg(feature = "vaapi")]
fn software_sps_pps(width: u32, height: u32) -> Vec<u8> {
    const SC: [u8; 4] = [0, 0, 0, 1];
    let (sps, pps): (&[u8], &[u8]) = match (width, height) {
        (320, 240) => (
            &[
                0x67, 0xf4, 0x00, 0x0d, 0x91, 0x96, 0x81, 0x41, 0xfb, 0x01, 0x10, 0x00, 0x00, 0x03,
                0x00, 0x10, 0x00, 0x00, 0x03, 0x03, 0xc0, 0xf1, 0x42, 0xaa,
            ],
            &[0x68, 0xce, 0x0f, 0x19],
        ),
        (640, 480) => (
            &[
                0x67, 0xf4, 0x00, 0x1e, 0x91, 0x96, 0x80, 0xa0, 0x3d, 0xb0, 0x11, 0x00, 0x00, 0x03,
                0x00, 0x10, 0x00, 0x00, 0x03, 0x03, 0xc0, 0xf1, 0x62, 0xea, 0x00,
            ],
            &[0x68, 0xce, 0x0f, 0x19],
        ),
        (1280, 720) => (
            &[
                0x67, 0xf4, 0x00, 0x1f, 0x91, 0x96, 0x80, 0x50, 0x05, 0xbb, 0x01, 0x10, 0x00, 0x00,
                0x03, 0x00, 0x10, 0x00, 0x00, 0x03, 0x03, 0xc0, 0xf1, 0x83, 0x2a,
            ],
            &[0x68, 0xce, 0x0f, 0x19],
        ),
        _ => (
            &[
                0x67, 0xf4, 0x00, 0x1e, 0x91, 0x96, 0x80, 0xa0, 0x3d, 0xb0, 0x11, 0x00, 0x00, 0x03,
                0x00, 0x10, 0x00, 0x00, 0x03, 0x03, 0xc0, 0xf1, 0x62, 0xea, 0x00,
            ],
            &[0x68, 0xce, 0x0f, 0x19],
        ),
    };
    let mut out = Vec::with_capacity(64);
    out.extend_from_slice(&SC);
    out.extend_from_slice(sps);
    out.extend_from_slice(&SC);
    out.extend_from_slice(pps);
    out
}
