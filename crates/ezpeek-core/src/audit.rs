//! Audit logging: security-relevant events with correlation IDs.
//! Rules (data_handling.md): structured, redacted, no PII/secrets,
//! event *types* only — never key codes, coordinates, pixel data,
//! SDP contents, or pairing codes.

use std::sync::atomic::{AtomicU64, Ordering};

static CORR: AtomicU64 = AtomicU64::new(1);

pub fn correlation_id() -> u64 {
    CORR.fetch_add(1, Ordering::Relaxed)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuditEvent {
    pub corr: u64,
    pub kind: &'static str,
    pub detail: AuditDetail,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AuditDetail {
    CodecSelected {
        codec: &'static str,
        path: &'static str,
    },
    PairingAccepted {
        code_len: usize,
    },
    PairingRejected {
        reason: &'static str,
    },
    HandshakeMessage {
        msg_type: &'static str,
    },
    InputInjected {
        event_type: &'static str,
    },
    RelayAllocated {
        realm_chars: usize,
    },
    RateLimited,
}

impl AuditEvent {
    pub fn log_line(&self) -> String {
        format!(
            "corr={} kind={} {}",
            self.corr,
            self.kind,
            self.detail.line()
        )
    }
}

impl AuditDetail {
    fn line(&self) -> String {
        match self {
            AuditDetail::CodecSelected { codec, path } => {
                format!("codec={codec} path={path}")
            }
            AuditDetail::PairingAccepted { code_len } => {
                format!("code_len={code_len}")
            }
            AuditDetail::PairingRejected { reason } => {
                format!("reason={reason}")
            }
            AuditDetail::HandshakeMessage { msg_type } => {
                format!("msg={msg_type}")
            }
            AuditDetail::InputInjected { event_type } => {
                format!("event={event_type}")
            }
            AuditDetail::RelayAllocated { realm_chars } => {
                format!("realm_chars={realm_chars}")
            }
            AuditDetail::RateLimited => "limited".to_string(),
        }
    }
}

pub fn emit(kind: &'static str, detail: AuditDetail) -> AuditEvent {
    let ev = AuditEvent {
        corr: correlation_id(),
        kind,
        detail,
    };
    eprintln!("audit {}", ev.log_line());
    ev
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn log_lines_carry_no_secrets() {
        let ev = emit("pairing", AuditDetail::PairingAccepted { code_len: 9 });
        let line = ev.log_line();
        assert!(line.contains("corr="));
        assert!(line.contains("code_len=9"));
        assert!(!line.contains("test-code-123"));
    }

    #[test]
    fn input_logs_type_only() {
        let ev = emit("input", AuditDetail::InputInjected { event_type: "Key" });
        let line = ev.log_line();
        assert!(line.contains("event=Key"));
        assert!(!line.contains("12345"));
    }

    #[test]
    fn handshake_logs_msg_type_only() {
        let ev = emit(
            "handshake",
            AuditDetail::HandshakeMessage { msg_type: "Offer" },
        );
        assert!(ev.log_line().contains("msg=Offer"));
    }

    #[test]
    fn correlation_ids_increase() {
        let a = correlation_id();
        let b = correlation_id();
        assert!(b > a);
    }
}
