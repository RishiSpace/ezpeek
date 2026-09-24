use ezpeek_core::EzpeekError;
use ezpeek_handshake::{decode_msg, encode_msg, HandshakeMsg, PairingCode};

pub struct HandshakeClient {
    pub rendezvous_url: String,
    pub pairing_code: PairingCode,
}

impl HandshakeClient {
    pub fn new(rendezvous_url: String, pairing_code: PairingCode) -> Self {
        Self {
            rendezvous_url,
            pairing_code,
        }
    }

    pub fn offer_msg(
        &self,
        sdp: String,
        capability: ezpeek_core::Capability,
    ) -> Result<String, EzpeekError> {
        let payload = ezpeek_handshake::CapabilityPayload {
            pairing_code: self.pairing_code.0.clone(),
            capability,
        };
        encode_msg(&HandshakeMsg::Offer {
            sdp,
            capabilities: payload,
        })
        .map_err(|e| EzpeekError::Handshake(format!("encode offer: {e}")))
    }

    pub fn answer_msg(
        &self,
        sdp: String,
        capability: ezpeek_core::Capability,
    ) -> Result<String, EzpeekError> {
        let payload = ezpeek_handshake::CapabilityPayload {
            pairing_code: self.pairing_code.0.clone(),
            capability,
        };
        encode_msg(&HandshakeMsg::Answer {
            sdp,
            capabilities: payload,
        })
        .map_err(|e| EzpeekError::Handshake(format!("encode answer: {e}")))
    }

    pub fn candidate_msg(&self, candidate: String) -> Result<String, EzpeekError> {
        encode_msg(&HandshakeMsg::IceCandidate { candidate })
            .map_err(|e| EzpeekError::Handshake(format!("encode candidate: {e}")))
    }

    pub fn parse_msg(&self, raw: &str) -> Result<HandshakeMsg, EzpeekError> {
        decode_msg(raw).map_err(|e| EzpeekError::Handshake(format!("decode msg: {e}")))
    }

    pub fn extract_sdp(&self, msg: &HandshakeMsg) -> Result<(bool, String), EzpeekError> {
        match msg {
            HandshakeMsg::Offer { sdp, .. } => Ok((true, sdp.clone())),
            HandshakeMsg::Answer { sdp, .. } => Ok((false, sdp.clone())),
            _ => Err(EzpeekError::Handshake("expected offer/answer".into())),
        }
    }

    pub fn extract_candidate(&self, msg: &HandshakeMsg) -> Result<String, EzpeekError> {
        match msg {
            HandshakeMsg::IceCandidate { candidate } => Ok(candidate.clone()),
            _ => Err(EzpeekError::Handshake("expected candidate".into())),
        }
    }
}
