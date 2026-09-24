//! XDG portal screen-cast session helper.
//! Opens a portal ScreenCast session (user picks monitor/window in the
//! system dialog), starts it, and returns the PipeWire node id(s).
//! Feature: `portal`. Non-interactive/headless use passes
//! `--node-id` directly to skip the dialog.

#[cfg(feature = "portal")]
use ezpeek_core::EzpeekError;

#[cfg(feature = "portal")]
pub struct PortalSession {
    pub node_ids: Vec<u32>,
    pub width: u32,
    pub height: u32,
}

#[cfg(feature = "portal")]
pub async fn request_screencast() -> Result<PortalSession, EzpeekError> {
    use ashpd::desktop::screencast::{
        CursorMode, Screencast, SelectSourcesOptions, SourceType, StartCastOptions,
    };
    let proxy = Screencast::new()
        .await
        .map_err(|e| EzpeekError::Capture(format!("portal connect: {e}")))?;
    let session: ashpd::desktop::Session<Screencast> = proxy
        .create_session(Default::default())
        .await
        .map_err(|e| EzpeekError::Capture(format!("portal session: {e}")))?;
    let select = SelectSourcesOptions::default()
        .set_cursor_mode(Some(CursorMode::Embedded))
        .set_sources(Some(SourceType::Monitor.into()))
        .set_multiple(Some(true));
    proxy
        .select_sources(&session, select)
        .await
        .map_err(|e| EzpeekError::Capture(format!("portal select: {e}")))?;
    let response = proxy
        .start(&session, None, StartCastOptions::default())
        .await
        .map_err(|e| EzpeekError::Capture(format!("portal start: {e}")))?
        .response()
        .map_err(|e| EzpeekError::Capture(format!("portal response: {e}")))?;
    let streams = response.streams();
    if streams.is_empty() {
        return Err(EzpeekError::Capture("portal: no streams selected".into()));
    }
    let node_ids: Vec<u32> = streams.iter().map(|s| s.pipe_wire_node_id()).collect();
    let (width, height) = streams.iter().find_map(|s| s.size()).unwrap_or((1280, 720));
    Ok(PortalSession {
        node_ids,
        width: width as u32,
        height: height as u32,
    })
}
