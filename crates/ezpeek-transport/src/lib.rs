use bytes::Bytes;
use ezpeek_core::{EzpeekError, VideoCodec};

pub mod handshake;
pub mod jitter;

#[derive(Debug, Clone)]
pub struct IceConfig {
    pub stun_urls: Vec<String>,
    pub turn_url: Option<String>,
    pub turn_username: Option<String>,
    pub turn_credential: Option<String>,
}

impl Default for IceConfig {
    fn default() -> Self {
        Self {
            stun_urls: vec!["stun:stun.l.google.com:19302".to_string()],
            turn_url: None,
            turn_username: None,
            turn_credential: None,
        }
    }
}

impl IceConfig {
    pub fn with_turn(mut self, url: String, username: String, credential: String) -> Self {
        self.turn_url = Some(url);
        self.turn_username = Some(username);
        self.turn_credential = Some(credential);
        self
    }

    pub fn direct_only(mut self) -> Self {
        self.turn_url = None;
        self.turn_username = None;
        self.turn_credential = None;
        self
    }

    pub fn relay_only(mut self) -> Self {
        self.stun_urls.clear();
        self
    }
}

pub struct PeerConnection {
    ice: IceConfig,
    #[cfg(feature = "webrtc-peer")]
    inner: Option<std::sync::Arc<webrtc::peer_connection::RTCPeerConnection>>,
}

impl PeerConnection {
    pub fn new(ice: IceConfig) -> Self {
        Self {
            ice,
            #[cfg(feature = "webrtc-peer")]
            inner: None,
        }
    }

    pub fn ice_config(&self) -> &IceConfig {
        &self.ice
    }

    #[cfg(feature = "webrtc-peer")]
    pub async fn connect(&mut self) -> Result<(), EzpeekError> {
        use webrtc::api::APIBuilder;
        use webrtc::ice_transport::ice_server::RTCIceServer;
        use webrtc::peer_connection::configuration::RTCConfiguration;

        let mut ice_servers = Vec::new();
        if !self.ice.stun_urls.is_empty() {
            ice_servers.push(RTCIceServer {
                urls: self.ice.stun_urls.clone(),
                ..Default::default()
            });
        }
        if let Some(turn) = &self.ice.turn_url {
            ice_servers.push(RTCIceServer {
                urls: vec![turn.clone()],
                username: self.ice.turn_username.clone().unwrap_or_default(),
                credential: self.ice.turn_credential.clone().unwrap_or_default(),
                ..Default::default()
            });
        }
        let api = APIBuilder::default().build();
        let pc = api
            .new_peer_connection(RTCConfiguration {
                ice_servers,
                ..Default::default()
            })
            .await
            .map_err(|e| EzpeekError::Transport(format!("webrtc new pc: {e}")))?;
        let input_dc = pc
            .create_data_channel("input", None)
            .await
            .map_err(|e| EzpeekError::Transport(format!("webrtc datachannel: {e}")))?;
        let _ = input_dc;
        self.inner = Some(std::sync::Arc::new(pc));
        Ok(())
    }

    #[cfg(feature = "webrtc-peer")]
    pub async fn create_offer(&self) -> Result<String, EzpeekError> {
        let pc = self
            .inner
            .as_ref()
            .ok_or_else(|| EzpeekError::Transport("call connect() first".into()))?;
        let offer = pc
            .create_offer(None)
            .await
            .map_err(|e| EzpeekError::Transport(format!("create offer: {e}")))?;
        pc.set_local_description(offer.clone())
            .await
            .map_err(|e| EzpeekError::Transport(format!("set local: {e}")))?;
        let mut gather = pc.gathering_complete_promise().await;
        let _ = tokio::time::timeout(std::time::Duration::from_secs(5), gather.recv()).await;
        let local = pc
            .local_description()
            .await
            .ok_or_else(|| EzpeekError::Transport("no local description".into()))?;
        Ok(local.sdp)
    }

    #[cfg(feature = "webrtc-peer")]
    pub async fn accept_offer_create_answer(&self, offer_sdp: &str) -> Result<String, EzpeekError> {
        use webrtc::peer_connection::sdp::session_description::RTCSessionDescription;
        let pc = self
            .inner
            .as_ref()
            .ok_or_else(|| EzpeekError::Transport("call connect() first".into()))?;
        pc.set_remote_description(
            RTCSessionDescription::offer(offer_sdp.to_owned())
                .map_err(|e| EzpeekError::Transport(format!("parse offer: {e}")))?,
        )
        .await
        .map_err(|e| EzpeekError::Transport(format!("set remote offer: {e}")))?;
        let answer = pc
            .create_answer(None)
            .await
            .map_err(|e| EzpeekError::Transport(format!("create answer: {e}")))?;
        pc.set_local_description(answer.clone())
            .await
            .map_err(|e| EzpeekError::Transport(format!("set local answer: {e}")))?;
        let mut gather = pc.gathering_complete_promise().await;
        let _ = tokio::time::timeout(std::time::Duration::from_secs(5), gather.recv()).await;
        let local = pc
            .local_description()
            .await
            .ok_or_else(|| EzpeekError::Transport("no local answer".into()))?;
        Ok(local.sdp)
    }

    #[cfg(feature = "webrtc-peer")]
    pub async fn accept_answer(&self, answer_sdp: &str) -> Result<(), EzpeekError> {
        use webrtc::peer_connection::sdp::session_description::RTCSessionDescription;
        let pc = self
            .inner
            .as_ref()
            .ok_or_else(|| EzpeekError::Transport("call connect() first".into()))?;
        pc.set_remote_description(
            RTCSessionDescription::answer(answer_sdp.to_owned())
                .map_err(|e| EzpeekError::Transport(format!("parse answer: {e}")))?,
        )
        .await
        .map_err(|e| EzpeekError::Transport(format!("set remote answer: {e}")))?;
        Ok(())
    }

    #[cfg(feature = "webrtc-peer")]
    pub async fn add_remote_candidate(&self, candidate: &str) -> Result<(), EzpeekError> {
        use webrtc::ice_transport::ice_candidate::RTCIceCandidateInit;
        let pc = self
            .inner
            .as_ref()
            .ok_or_else(|| EzpeekError::Transport("call connect() first".into()))?;
        pc.add_ice_candidate(RTCIceCandidateInit {
            candidate: candidate.to_owned(),
            ..Default::default()
        })
        .await
        .map_err(|e| EzpeekError::Transport(format!("add candidate: {e}")))?;
        Ok(())
    }

    #[cfg(feature = "webrtc-peer")]
    pub fn on_local_candidate(
        &self,
        f: impl Fn(String) + Send + Sync + 'static,
    ) -> Result<(), EzpeekError> {
        use std::sync::Arc;
        let pc = self
            .inner
            .as_ref()
            .ok_or_else(|| EzpeekError::Transport("call connect() first".into()))?;
        let f = Arc::new(f);
        pc.on_ice_candidate(Box::new(move |c| {
            let f = Arc::clone(&f);
            Box::pin(async move {
                if let Some(c) = c {
                    if let Ok(json) = c.to_json() {
                        f(json.candidate);
                    }
                }
            })
        }));
        Ok(())
    }

    #[cfg(feature = "webrtc-peer")]
    pub async fn create_video_track(
        &self,
    ) -> Result<
        std::sync::Arc<
            webrtc::track::track_local::track_local_static_sample::TrackLocalStaticSample,
        >,
        EzpeekError,
    > {
        use webrtc::api::media_engine::MIME_TYPE_H264;
        use webrtc::rtp_transceiver::rtp_codec::RTCRtpCodecCapability;
        use webrtc::track::track_local::track_local_static_sample::TrackLocalStaticSample;
        use webrtc::track::track_local::TrackLocal;
        let pc = self
            .inner
            .as_ref()
            .ok_or_else(|| EzpeekError::Transport("call connect() first".into()))?;
        let track = std::sync::Arc::new(TrackLocalStaticSample::new(
            RTCRtpCodecCapability {
                mime_type: MIME_TYPE_H264.to_owned(),
                ..Default::default()
            },
            "video".to_owned(),
            "ezpeek".to_owned(),
        ));
        pc.add_track(track.clone() as std::sync::Arc<dyn TrackLocal + Send + Sync>)
            .await
            .map_err(|e| EzpeekError::Transport(format!("add track: {e}")))?;
        Ok(track)
    }

    #[cfg(feature = "webrtc-peer")]
    pub async fn write_video_sample(
        track: &webrtc::track::track_local::track_local_static_sample::TrackLocalStaticSample,
        data: bytes::Bytes,
        duration: std::time::Duration,
    ) -> Result<(), EzpeekError> {
        use webrtc::media::Sample;
        track
            .write_sample(&Sample {
                data,
                duration,
                ..Default::default()
            })
            .await
            .map_err(|e| EzpeekError::Transport(format!("write sample: {e}")))
    }
}

pub fn pack_h264_annexb(nal: &[u8], seq: u32, timestamp: u32) -> Result<Bytes, EzpeekError> {
    if nal.is_empty() {
        return Err(EzpeekError::InvalidArgument("empty NAL".to_string()));
    }
    let mut out = Vec::with_capacity(nal.len() + 12);
    out.extend_from_slice(&seq.to_be_bytes());
    out.extend_from_slice(&timestamp.to_be_bytes());
    out.extend_from_slice(&(nal.len() as u32).to_be_bytes());
    out.extend_from_slice(nal);
    Ok(Bytes::from(out))
}

pub fn unpack_rtp_payload(pkt: &[u8]) -> Result<(u32, u32, &[u8]), EzpeekError> {
    if pkt.len() < 12 {
        return Err(EzpeekError::InvalidArgument("packet too short".to_string()));
    }
    let seq = u32::from_be_bytes(pkt[0..4].try_into().unwrap());
    let timestamp = u32::from_be_bytes(pkt[4..8].try_into().unwrap());
    let len = u32::from_be_bytes(pkt[8..12].try_into().unwrap()) as usize;
    if pkt.len() < 12 + len {
        return Err(EzpeekError::InvalidArgument("truncated NAL".to_string()));
    }
    Ok((seq, timestamp, &pkt[12..12 + len]))
}

pub fn codec_supported(codec: VideoCodec) -> bool {
    matches!(codec, VideoCodec::H264 | VideoCodec::Av1)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn h264_pack_round_trip() {
        let nal = vec![0x65, 0x01, 0x02, 0x03];
        let pkt = pack_h264_annexb(&nal, 7, 90000).unwrap();
        let (seq, ts, out) = unpack_rtp_payload(&pkt).unwrap();
        assert_eq!((seq, ts), (7, 90000));
        assert_eq!(out, nal.as_slice());
    }

    #[test]
    fn pack_rejects_empty() {
        assert!(pack_h264_annexb(&[], 0, 0).is_err());
    }

    #[test]
    fn unpack_rejects_short() {
        assert!(unpack_rtp_payload(&[0u8; 5]).is_err());
    }
}
