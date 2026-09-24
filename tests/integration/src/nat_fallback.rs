//! NAT/connectivity tests: direct P2P vs TURN fallback.
//! Run: `cargo test -p ezpeek-integration -- --ignored`
//! Requires: loopback interfaces; forced-relay uses relay_only ICE config.

#[cfg(test)]
use ezpeek_transport::IceConfig;

#[test]
fn ice_config_direct_only_has_no_turn() {
    let cfg = IceConfig::default().direct_only();
    assert!(!cfg.stun_urls.is_empty());
    assert!(cfg.turn_url.is_none());
}

#[test]
fn ice_config_relay_only_has_no_stun() {
    let cfg = IceConfig::default()
        .with_turn(
            "turn:127.0.0.1:3478".to_string(),
            "user".to_string(),
            "pass".to_string(),
        )
        .relay_only();
    assert!(cfg.stun_urls.is_empty());
    assert!(cfg.turn_url.is_some());
    assert_eq!(cfg.turn_username.as_deref(), Some("user"));
}

#[test]
#[ignore]
fn direct_p2p_succeeds_when_reachable() {
    unimplemented!("needs live rendezvous + two webrtc peers on loopback");
}

#[test]
#[ignore]
fn forced_relay_falls_back_to_turn() {
    unimplemented!("needs live TURN relay + relay_only ICE on both peers");
}

#[cfg(feature = "turn-relay-probe")]
#[tokio::test]
async fn turn_relay_answers_stun_binding() {
    use std::net::UdpSocket;
    let url = std::env::var("TURN_PROBE_ADDR").unwrap_or("127.0.0.1:3478".into());
    let sock = UdpSocket::bind("127.0.0.1:0").unwrap();
    sock.set_read_timeout(Some(std::time::Duration::from_secs(3)))
        .unwrap();
    let mut req = vec![0x00u8, 0x01, 0x00, 0x00, 0x21, 0x12, 0xa4, 0x42];
    req.extend_from_slice(&[0u8; 12]);
    sock.send_to(&req, &url).unwrap();
    let mut buf = [0u8; 1024];
    let (n, _) = sock.recv_from(&mut buf).unwrap();
    assert!(n >= 20, "TURN/STUN response too short");
    assert_eq!(buf[0], 0x01, "expected success response class");
}
