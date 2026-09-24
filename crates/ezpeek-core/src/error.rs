use thiserror::Error;

#[derive(Debug, Error)]
pub enum EzpeekError {
    #[error("unsupported on this platform/backend: {0}")]
    Unsupported(&'static str),
    #[error("capture failed: {0}")]
    Capture(String),
    #[error("encode failed: {0}")]
    Encode(String),
    #[error("decode failed: {0}")]
    Decode(String),
    #[error("transport failed: {0}")]
    Transport(String),
    #[error("handshake failed: {0}")]
    Handshake(String),
    #[error("input failed: {0}")]
    Input(String),
    #[error("present failed: {0}")]
    Present(String),
    #[error("invalid argument: {0}")]
    InvalidArgument(String),
    #[error("pool exhausted")]
    PoolExhausted,
    #[error("fd ownership error: {0}")]
    FdOwnership(&'static str),
}
