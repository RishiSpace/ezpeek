//! SDP offer/answer loopback: host creates offer, viewer answers,
//! both set remote descriptions, ICE trickles over in-memory channel.
//! Run: `cargo test -p ezpeek-integration --features webrtc-loopback`

#[cfg(feature = "webrtc-loopback")]
use ezpeek_handshake::PairingCode;
#[cfg(feature = "webrtc-loopback")]
use ezpeek_transport::{handshake::HandshakeClient, IceConfig, PeerConnection};

#[cfg(feature = "webrtc-loopback")]
fn test_capability() -> ezpeek_core::Capability {
    ezpeek_core::Capability {
        encode_hw_h264: true,
        decode_hw_h264: true,
        encode_hw_av1: false,
        decode_hw_av1: false,
        max_width: 1280,
        max_height: 720,
        max_fps: 30,
        bandwidth_bps: 10_000_000,
    }
}

#[cfg(feature = "webrtc-loopback")]
#[tokio::test]
async fn sdp_offer_answer_loopback() {
    let code = PairingCode::new("test-loopback").unwrap();
    let host_cli = HandshakeClient::new("ws://127.0.0.1:1/ws".into(), code.clone());
    let viewer_cli = HandshakeClient::new("ws://127.0.0.1:1/ws".into(), code);

    let mut host = PeerConnection::new(IceConfig::default().direct_only());
    let mut viewer = PeerConnection::new(IceConfig::default().direct_only());
    host.connect().await.unwrap();
    viewer.connect().await.unwrap();

    let offer_sdp = host.create_offer().await.unwrap();
    assert!(offer_sdp.contains("v=0"), "offer must be SDP");
    let offer_wire = host_cli
        .offer_msg(offer_sdp.clone(), test_capability())
        .unwrap();
    let parsed = viewer_cli.parse_msg(&offer_wire).unwrap();
    let (is_offer, got_sdp) = viewer_cli.extract_sdp(&parsed).unwrap();
    assert!(is_offer);
    assert_eq!(got_sdp, offer_sdp);

    let answer_sdp = viewer.accept_offer_create_answer(&got_sdp).await.unwrap();
    assert!(answer_sdp.contains("v=0"), "answer must be SDP");
    let answer_wire = viewer_cli
        .answer_msg(answer_sdp.clone(), test_capability())
        .unwrap();
    let parsed = host_cli.parse_msg(&answer_wire).unwrap();
    let (is_offer, got_answer) = host_cli.extract_sdp(&parsed).unwrap();
    assert!(!is_offer);
    host.accept_answer(&got_answer).await.unwrap();

    let cand_wire = host_cli
        .candidate_msg("candidate:1 1 udp 1 127.0.0.1 5000 typ host".into())
        .unwrap();
    let parsed = viewer_cli.parse_msg(&cand_wire).unwrap();
    let cand = viewer_cli.extract_candidate(&parsed).unwrap();
    assert!(cand.contains("candidate:"));
}
