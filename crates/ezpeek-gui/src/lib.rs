use ezpeek_core::VideoCodec;

#[derive(Debug, Clone, Default)]
pub struct StatusPanel {
    pub codec: Option<VideoCodec>,
    pub rtt_ms: Option<u64>,
    pub relayed: bool,
}

impl StatusPanel {
    pub fn summary(&self) -> String {
        let codec = match self.codec {
            Some(VideoCodec::H264) => "H.264",
            Some(VideoCodec::Av1) => "AV1",
            None => "—",
        };
        let path = if self.relayed { "relay" } else { "direct" };
        let rtt = self.rtt_ms.map(|r| format!("{r}ms")).unwrap_or_default();
        format!("codec={codec} path={path} rtt={rtt}")
    }
}
