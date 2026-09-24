use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::Result;
use axum::extract::ws::{Message, WebSocket};
use axum::extract::{ConnectInfo, State, WebSocketUpgrade};
use axum::response::IntoResponse;
use axum::routing::get;
use ezpeek_handshake::{decode_msg, encode_msg, HandshakeMsg, PairingCode};
use tokio::sync::Mutex;

const SESSION_TTL: Duration = Duration::from_secs(300);
const MAX_SESSIONS: usize = 1024;
const MAX_QUEUE_PER_SESSION: usize = 64;
const MAX_MSG_BYTES: usize = 65536;
const RATE_WINDOW: Duration = Duration::from_secs(60);
const RATE_MAX_MSGS: u32 = 120;

#[derive(Clone)]
struct AppState {
    sessions: Arc<Mutex<HashMap<String, SessionEntry>>>,
    rates: Arc<Mutex<HashMap<SocketAddr, RateEntry>>>,
}

struct SessionEntry {
    created: Instant,
    queue: Vec<String>,
}

struct RateEntry {
    window_start: Instant,
    count: u32,
}

impl AppState {
    fn new() -> Self {
        Self {
            sessions: Arc::new(Mutex::new(HashMap::new())),
            rates: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    async fn check_rate(&self, addr: SocketAddr) -> bool {
        let mut rates = self.rates.lock().await;
        let now = Instant::now();
        let entry = rates.entry(addr).or_insert(RateEntry {
            window_start: now,
            count: 0,
        });
        if now.duration_since(entry.window_start) > RATE_WINDOW {
            entry.window_start = now;
            entry.count = 0;
        }
        entry.count += 1;
        entry.count <= RATE_MAX_MSGS
    }

    async fn push(&self, code: &str, raw: String) -> bool {
        let mut sessions = self.sessions.lock().await;
        if sessions.len() >= MAX_SESSIONS && !sessions.contains_key(code) {
            return false;
        }
        let entry = sessions.entry(code.to_string()).or_insert(SessionEntry {
            created: Instant::now(),
            queue: Vec::new(),
        });
        if entry.created.elapsed() > SESSION_TTL {
            entry.created = Instant::now();
            entry.queue.clear();
        }
        entry.queue.push(raw);
        if entry.queue.len() > MAX_QUEUE_PER_SESSION {
            entry.queue.remove(0);
        }
        true
    }

    async fn drain(&self, code: &str) -> Vec<String> {
        let mut sessions = self.sessions.lock().await;
        match sessions.get_mut(code) {
            Some(e) if e.created.elapsed() <= SESSION_TTL => std::mem::take(&mut e.queue),
            Some(_) => {
                sessions.remove(code);
                Vec::new()
            }
            None => Vec::new(),
        }
    }
}

fn parse_arg(args: &[String], name: &str) -> Option<String> {
    args.windows(2).find(|w| w[0] == name).map(|w| w[1].clone())
}

#[tokio::main]
async fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|a| a == "--help" || a == "-h") {
        println!("ezpeek-rendezvous: handshake relay + fallback TURN (Milestone 4).");
        println!("  --listen ADDR (default 127.0.0.1:7447) --ttl SECS");
        println!("  --turn-listen ADDR (default 127.0.0.1:3478; requires --features turn-relay)");
        println!("  --turn-realm NAME --turn-user USER --turn-pass PASS");
        println!("  TURN relay is fallback-only: used iff ICE finds no direct path.");
        return Ok(());
    }
    let listen = parse_arg(&args, "--listen").unwrap_or_else(|| "127.0.0.1:7447".to_string());
    let turn_listen =
        parse_arg(&args, "--turn-listen").unwrap_or_else(|| "127.0.0.1:3478".to_string());
    let turn_realm = parse_arg(&args, "--turn-realm").unwrap_or_else(|| "ezpeek".to_string());
    #[cfg(feature = "turn-relay")]
    {
        let turn_user = parse_arg(&args, "--turn-user").unwrap_or_else(|| "ezpeek".to_string());
        let turn_pass = parse_arg(&args, "--turn-pass").unwrap_or_default();
        if turn_pass.is_empty() {
            eprintln!("warning: --turn-pass empty, TURN relay will reject all allocations");
        }
        let relay = turn_listen.clone();
        let realm = turn_realm.clone();
        tokio::spawn(async move {
            if let Err(e) = run_turn_relay(&relay, &realm, &turn_user, &turn_pass).await {
                eprintln!("turn relay exited: {e:#}");
            }
        });
    }
    #[cfg(not(feature = "turn-relay"))]
    {
        let _ = (turn_listen, turn_realm);
    }
    let state = AppState::new();
    let app = axum::Router::new()
        .route("/ws", get(ws_handler))
        .route("/health", get(|| async { "ok" }))
        .with_state(state);
    eprintln!(
        "ezpeek-rendezvous listening on {listen} (handshake only; no DTLS termination, no media)"
    );
    let listener = tokio::net::TcpListener::bind(&listen).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

async fn ws_handler(
    ws: WebSocketUpgrade,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    State(state): State<AppState>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| handle_socket(socket, state, addr))
}

#[cfg(feature = "turn-relay")]
async fn run_turn_relay(listen: &str, realm: &str, user: &str, pass: &str) -> Result<()> {
    use std::net::SocketAddr;
    use std::sync::Arc;
    let addr: SocketAddr = listen
        .parse()
        .map_err(|e| anyhow::anyhow!("bad --turn-listen {listen}: {e}"))?;
    let conn = tokio::net::UdpSocket::bind(addr).await?;
    eprintln!("turn relay listening on {addr} realm={realm} (fallback only)");
    let user = user.to_owned();
    let pass = pass.to_owned();
    let realm = realm.to_owned();
    struct StaticAuth {
        user: String,
        pass: String,
        realm: String,
    }
    impl turn::auth::AuthHandler for StaticAuth {
        fn auth_handle(
            &self,
            username: &str,
            _realm: &str,
            _src_addr: SocketAddr,
        ) -> Result<Vec<u8>, turn::Error> {
            if username == self.user && !self.pass.is_empty() {
                Ok(turn::auth::generate_auth_key(
                    username,
                    &self.realm,
                    &self.pass,
                ))
            } else {
                Err(turn::Error::ErrRelayAddressInvalid)
            }
        }
    }
    let config = turn::server::config::ServerConfig {
        conn_configs: vec![turn::server::config::ConnConfig {
            conn: Arc::new(conn),
            relay_addr_generator: Box::new(
                turn::relay::relay_static::RelayAddressGeneratorStatic {
                    relay_address: addr.ip(),
                    address: "0.0.0.0".to_string(),
                    net: Arc::new(webrtc_util::vnet::net::Net::new(None)),
                },
            ),
        }],
        realm: realm.clone(),
        auth_handler: Arc::new(StaticAuth { user, pass, realm }),
        channel_bind_timeout: std::time::Duration::from_secs(600),
    };
    let server = turn::server::Server::new(config).await?;
    std::future::pending::<()>().await;
    server.close().await?;
    Ok(())
}

async fn send_error(socket: &mut WebSocket, message: &str) {
    let err = encode_msg(&HandshakeMsg::Error {
        message: message.to_string(),
    })
    .unwrap_or_else(|_| r#"{"type":"Error","message":"error"}"#.to_string());
    let _ = socket.send(Message::Text(err.into())).await;
}

async fn handle_socket(mut socket: WebSocket, state: AppState, addr: SocketAddr) {
    let mut my_code: Option<String> = None;
    loop {
        tokio::select! {
            msg = socket.recv() => {
                let Some(msg) = msg else { break };
                let Ok(msg) = msg else { break };
                if !state.check_rate(addr).await {
                    send_error(&mut socket, "rate limited").await;
                    break;
                }
                let text = match msg {
                    Message::Text(t) => t.to_string(),
                    Message::Close(_) => break,
                    _ => continue,
                };
                if text.len() > MAX_MSG_BYTES {
                    send_error(&mut socket, "message too large").await;
                    continue;
                }
                let parsed = match decode_msg(&text) {
                    Ok(m) => m,
                    Err(_) => {
                        send_error(&mut socket, "invalid handshake message").await;
                        continue;
                    }
                };
                let code = match &parsed {
                    HandshakeMsg::Offer { capabilities, .. }
                    | HandshakeMsg::Answer { capabilities, .. } => {
                        Some(capabilities.pairing_code.clone())
                    }
                    HandshakeMsg::IceCandidate { .. } => my_code.clone(),
                    HandshakeMsg::Error { .. } => None,
                };
                let Some(code) = code else {
                    send_error(&mut socket, "pairing code required").await;
                    continue;
                };
                if PairingCode::new(code.clone()).is_err() {
                    send_error(&mut socket, "invalid pairing code").await;
                    continue;
                }
                my_code = Some(code.clone());
                if !state.push(&code, text).await {
                    send_error(&mut socket, "server busy").await;
                    continue;
                }
                for queued in state.drain(&code).await {
                    let msg: axum::extract::ws::Utf8Bytes = queued.into();
                    if socket.send(Message::Text(msg)).await.is_err() {
                        break;
                    }
                }
            }
        }
    }
}
