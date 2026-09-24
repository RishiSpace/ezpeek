//! NVIDIA NVENC hardware encoder backend.
//! Target: Windows/Linux, NVIDIA GPUs via Video Codec SDK 12.x,
//! bound through `moq-nvenc` (dlopen, no link-time SDK needed).
//! (developer.nvidia.com/video-codec-sdk).
//! Config: P1 low-latency preset, ultra-low-latency tuning, no B-frames,
//! CBR-ish low-latency rate control, short GOP. Feature: `nvenc`.

use ezpeek_core::{EzpeekError, GpuFrame, VideoCodec};

use crate::{BitstreamChunk, Encoder, LowLatencyConfig};

pub struct NvencEncoder {
    #[cfg(feature = "nvenc")]
    state: Option<NvencState>,
    #[cfg(feature = "nvenc")]
    width: u32,
    #[cfg(feature = "nvenc")]
    height: u32,
    #[cfg(feature = "nvenc")]
    fps: u32,
    #[cfg(feature = "nvenc")]
    av1: bool,
    cfg: LowLatencyConfig,
    #[cfg(feature = "nvenc")]
    seq: u64,
}

#[cfg(feature = "nvenc")]
struct NvencState {
    _session: moq_nvenc::Session,
    width: u32,
    height: u32,
}

impl NvencEncoder {
    pub fn new(_width: u32, _height: u32, _fps: u32) -> Self {
        Self {
            #[cfg(feature = "nvenc")]
            state: None,
            #[cfg(feature = "nvenc")]
            width: _width,
            #[cfg(feature = "nvenc")]
            height: _height,
            #[cfg(feature = "nvenc")]
            fps: _fps,
            #[cfg(feature = "nvenc")]
            av1: false,
            cfg: LowLatencyConfig::default(),
            #[cfg(feature = "nvenc")]
            seq: 0,
        }
    }

    pub fn with_codec(_width: u32, _height: u32, _fps: u32, _av1: bool) -> Self {
        Self {
            #[cfg(feature = "nvenc")]
            state: None,
            #[cfg(feature = "nvenc")]
            width: _width,
            #[cfg(feature = "nvenc")]
            height: _height,
            #[cfg(feature = "nvenc")]
            fps: _fps,
            #[cfg(feature = "nvenc")]
            av1: _av1,
            cfg: LowLatencyConfig::default(),
            #[cfg(feature = "nvenc")]
            seq: 0,
        }
    }

    #[cfg(feature = "nvenc")]
    fn ensure_open(&mut self) -> Result<(), EzpeekError> {
        use moq_nvenc::sys::nvEncodeAPI as nv;
        if self.state.is_some() {
            return Ok(());
        }
        let want_av1 = self.av1;
        let codec_guid = if want_av1 {
            nv::NV_ENC_CODEC_AV1_GUID
        } else {
            nv::NV_ENC_CODEC_H264_GUID
        };
        let cuda_ctx = cudarc::driver::CudaContext::new(0)
            .map_err(|e| EzpeekError::Encode(format!("nvenc cuda ctx: {e:?}")))?;
        let encoder = moq_nvenc::Encoder::initialize_with_cuda(cuda_ctx)
            .map_err(|e| EzpeekError::Encode(format!("nvenc init: {e:?}")))?;
        let guids = encoder
            .get_encode_guids()
            .map_err(|e| EzpeekError::Encode(format!("nvenc guids: {e:?}")))?;
        if !guids.contains(&codec_guid) {
            if want_av1 {
                return Err(EzpeekError::Encode(
                    "nvenc: no AV1 GUID on this GPU (needs Ada Lovelace+)".into(),
                ));
            }
            return Err(EzpeekError::Encode("nvenc: no H264 GUID".into()));
        }
        let preset_guids = encoder
            .get_preset_guids(codec_guid)
            .map_err(|e| EzpeekError::Encode(format!("nvenc presets: {e:?}")))?;
        let preset = preset_guids
            .iter()
            .find(|g| **g == nv::NV_ENC_PRESET_P1_GUID)
            .or(preset_guids.first())
            .ok_or_else(|| EzpeekError::Encode("nvenc: no presets".into()))?;
        let mut preset_cfg = encoder
            .get_preset_config(
                codec_guid,
                *preset,
                nv::NV_ENC_TUNING_INFO::NV_ENC_TUNING_INFO_ULTRA_LOW_LATENCY,
            )
            .map_err(|e| EzpeekError::Encode(format!("nvenc preset cfg: {e:?}")))?;
        {
            let rc = &mut preset_cfg.presetCfg.rcParams;
            rc.rateControlMode = nv::NV_ENC_PARAMS_RC_MODE::NV_ENC_PARAMS_RC_CBR;
            rc.averageBitRate = self.cfg.bitrate_bps.min(u32::MAX as u64) as u32;
            rc.maxBitRate = rc.averageBitRate;
            rc.vbvBufferSize = rc.averageBitRate / self.fps.max(1);
        }
        if !want_av1 {
            let cfg = unsafe { &mut preset_cfg.presetCfg.encodeCodecConfig.h264Config };
            cfg.maxNumRefFrames = 1;
            cfg.idrPeriod = self.cfg.gop_len;
            cfg.set_repeatSPSPPS(1);
        }
        preset_cfg.presetCfg.frameIntervalP = 1;
        preset_cfg.presetCfg.gopLength = self.cfg.gop_len;
        let mut init = moq_nvenc::EncoderInitParams::new(codec_guid, self.width, self.height);
        init.preset_guid(*preset)
            .tuning_info(nv::NV_ENC_TUNING_INFO::NV_ENC_TUNING_INFO_ULTRA_LOW_LATENCY)
            .encode_config(&mut preset_cfg.presetCfg)
            .framerate(self.fps, 1)
            .enable_picture_type_decision();
        let session = encoder
            .start_session(nv::NV_ENC_BUFFER_FORMAT::NV_ENC_BUFFER_FORMAT_NV12, init)
            .map_err(|e| EzpeekError::Encode(format!("nvenc session: {e:?}")))?;
        self.state = Some(NvencState {
            _session: session,
            width: self.width,
            height: self.height,
        });
        Ok(())
    }
}

impl Encoder for NvencEncoder {
    fn encode(&mut self, frame: &GpuFrame) -> Result<BitstreamChunk, EzpeekError> {
        #[cfg(not(feature = "nvenc"))]
        {
            let _ = frame;
            Err(EzpeekError::Unsupported(
                "nvenc: rebuild with --features nvenc",
            ))
        }
        #[cfg(feature = "nvenc")]
        {
            self.ensure_open()?;
            let st = self
                .state
                .as_mut()
                .ok_or_else(|| EzpeekError::Encode("nvenc not open".into()))?;
            let seq = self.seq;
            self.seq += 1;
            let mut input = st
                ._session
                .create_input_buffer()
                .map_err(|e| EzpeekError::Encode(format!("nvenc input buf: {e:?}")))?;
            let mut output = st
                ._session
                .create_output_bitstream()
                .map_err(|e| EzpeekError::Encode(format!("nvenc output buf: {e:?}")))?;
            {
                let mut lock = input
                    .lock()
                    .map_err(|e| EzpeekError::Encode(format!("nvenc lock: {e:?}")))?;
                let w = st.width as usize;
                let h = st.height as usize;
                let pitch = lock.pitch() as usize;
                match frame.handle {
                    ezpeek_core::FrameHandle::CpuNv12(ptr, len)
                        if !ptr.is_null() && len >= w * h * 3 / 2 =>
                    unsafe {
                        let src = std::slice::from_raw_parts(ptr, w * h * 3 / 2);
                        lock.write_rows(0, pitch, &src[..w * h], w, h);
                        lock.write_rows(pitch * h, pitch, &src[w * h..], w, h / 2);
                    },
                    _ => {
                        let y_row = vec![0x10u8; w];
                        let uv_row = vec![0x80u8; w];
                        unsafe {
                            for row in 0..h {
                                lock.write_rows(row * pitch, pitch, &y_row, w, 1);
                            }
                            let uv_base = pitch * h;
                            for row in 0..h / 2 {
                                lock.write_rows(uv_base + row * pitch, pitch, &uv_row, w, 1);
                            }
                        }
                    }
                }
            }
            st._session
                .encode_picture(
                    &mut input,
                    &mut output,
                    moq_nvenc::EncodePictureParams {
                        input_timestamp: frame.timestamp_ns,
                        force_idr: seq.is_multiple_of(self.cfg.gop_len as u64),
                        ..Default::default()
                    },
                )
                .map_err(|e| EzpeekError::Encode(format!("nvenc encode: {e:?}")))?;
            let lock = output
                .lock()
                .map_err(|e| EzpeekError::Encode(format!("nvenc output lock: {e:?}")))?;
            let is_key = matches!(
                lock.picture_type(),
                moq_nvenc::sys::nvEncodeAPI::NV_ENC_PIC_TYPE::NV_ENC_PIC_TYPE_IDR
                    | moq_nvenc::sys::nvEncodeAPI::NV_ENC_PIC_TYPE::NV_ENC_PIC_TYPE_I
            );
            Ok(BitstreamChunk {
                codec: if self.av1 {
                    VideoCodec::Av1
                } else {
                    VideoCodec::H264
                },
                data: lock.data().to_vec(),
                timestamp_ns: frame.timestamp_ns,
                seq,
                is_keyframe: is_key,
            })
        }
    }

    fn configure_low_latency(&mut self, cfg: &LowLatencyConfig) -> Result<(), EzpeekError> {
        self.cfg = *cfg;
        #[cfg(feature = "nvenc")]
        {
            self.state = None;
        }
        Ok(())
    }

    fn codec(&self) -> VideoCodec {
        #[cfg(feature = "nvenc")]
        if self.av1 {
            return VideoCodec::Av1;
        }
        VideoCodec::H264
    }
}
