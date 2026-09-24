use ezpeek_core::Capability;
use serde::{Deserialize, Serialize};
use std::time::{Duration, SystemTime};

pub const PAIRING_CODE_TTL: Duration = Duration::from_secs(300);
pub const PAIRING_CODE_LEN: usize = 9;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PairingCode(pub String);

impl PairingCode {
    pub fn new(code: impl Into<String>) -> Result<Self, String> {
        let code = code.into();
        if code.len() < 4 || code.len() > 64 {
            return Err("pairing code must be 4-64 chars".to_string());
        }
        if !code
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
        {
            return Err("pairing code must be alphanumeric, '-' or '_'".to_string());
        }
        Ok(Self(code))
    }

    pub fn generate() -> (Self, SystemTime) {
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};
        let mut h = DefaultHasher::new();
        SystemTime::now().hash(&mut h);
        std::process::id().hash(&mut h);
        let n = h.finish() % 1_000_000_000;
        let code = format!("{:09}", n);
        (Self(code), SystemTime::now())
    }

    pub fn is_expired(&self, issued_at: SystemTime) -> bool {
        SystemTime::now()
            .duration_since(issued_at)
            .map(|d| d > PAIRING_CODE_TTL)
            .unwrap_or(true)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CapabilityPayload {
    pub pairing_code: String,
    pub capability: Capability,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SdpOffer {
    pub sdp: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SdpAnswer {
    pub sdp: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum HandshakeMsg {
    Offer {
        sdp: String,
        capabilities: CapabilityPayload,
    },
    Answer {
        sdp: String,
        capabilities: CapabilityPayload,
    },
    IceCandidate {
        candidate: String,
    },
    Error {
        message: String,
    },
}

pub fn encode_msg(msg: &HandshakeMsg) -> Result<String, serde_json::Error> {
    serde_json::to_string(msg)
}

pub fn decode_msg(raw: &str) -> Result<HandshakeMsg, serde_json::Error> {
    serde_json::from_str(raw)
}

#[cfg(test)]
mod tests {
    use super::*;
    use ezpeek_core::VideoCodec;

    #[test]
    fn offer_round_trips() {
        let msg = HandshakeMsg::IceCandidate {
            candidate: "candidate:1 1 udp 1 127.0.0.1 5000 typ host".to_string(),
        };
        let raw = encode_msg(&msg).unwrap();
        let back = decode_msg(&raw).unwrap();
        assert!(matches!(back, HandshakeMsg::IceCandidate { .. }));
    }

    #[test]
    fn pairing_code_validated() {
        assert!(PairingCode::new("abc").is_err());
        assert!(PairingCode::new("abcd").is_ok());
        assert!(PairingCode::new("x".repeat(65)).is_err());
        assert!(PairingCode::new("ab cd").is_err());
        assert!(PairingCode::new("ab;cd").is_err());
        assert!(PairingCode::new("ab-cd_12").is_ok());
    }

    #[test]
    fn pairing_code_expiry() {
        let (code, issued) = PairingCode::generate();
        assert_eq!(code.0.len(), PAIRING_CODE_LEN);
        assert!(!code.is_expired(issued));
        let old = SystemTime::now() - PAIRING_CODE_TTL - Duration::from_secs(1);
        assert!(code.is_expired(old));
    }

    #[test]
    fn codec_policy_h264_baseline() {
        let host = Capability {
            encode_hw_h264: true,
            decode_hw_h264: true,
            encode_hw_av1: false,
            decode_hw_av1: false,
            max_width: 1920,
            max_height: 1080,
            max_fps: 60,
            bandwidth_bps: 10_000_000,
        };
        assert_eq!(ezpeek_core::select_codec(&host, &host), VideoCodec::H264);
    }
}
