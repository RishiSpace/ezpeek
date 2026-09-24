//! Linux PipeWire screencast-portal capture backend.
//! Target: Linux Wayland via xdg-desktop-portal → DMA-BUF fd.
//! Vendor SDK: libpipewire (pipewire.freedesktop.org).
//! Portal session setup (ashpd ScreenCast) is done by the caller to
//! obtain the PipeWire node id; this backend connects a stream to it.
//! Feature: `pipewire-capture`.

use ezpeek_core::EzpeekError;

#[cfg(not(feature = "pipewire-capture"))]
use ezpeek_core::GpuFrame;
#[cfg(feature = "pipewire-capture")]
use ezpeek_core::{now_ns, FrameHandle, GpuFrame, PixelFormat};

#[cfg(feature = "pipewire-capture")]
use std::sync::{Arc, Mutex};

pub struct PipeWireCapture {
    #[cfg(feature = "pipewire-capture")]
    inner: Option<PipeWireState>,
    #[allow(dead_code)]
    width: u32,
    #[allow(dead_code)]
    height: u32,
    #[allow(dead_code)]
    seq: u64,
}

#[cfg(feature = "pipewire-capture")]
struct PipeWireState {
    _mainloop: pipewire::main_loop::MainLoopRc,
    _core: pipewire::core::CoreRc,
    _stream: pipewire::stream::StreamBox<'static>,
    latest_fd: Arc<Mutex<Option<std::os::fd::OwnedFd>>>,
    width: Arc<Mutex<u32>>,
    height: Arc<Mutex<u32>>,
}

impl PipeWireCapture {
    pub fn new(width: u32, height: u32) -> Self {
        Self {
            #[cfg(feature = "pipewire-capture")]
            inner: None,
            width,
            height,
            seq: 0,
        }
    }

    pub fn connect_node(&mut self, _node_id: u32) -> Result<(), EzpeekError> {
        #[cfg(not(feature = "pipewire-capture"))]
        {
            Err(EzpeekError::Unsupported(
                "pipewire: rebuild with --features pipewire-capture",
            ))
        }
        #[cfg(feature = "pipewire-capture")]
        {
            use pipewire as pw;
            pw::init();
            let mainloop = pw::main_loop::MainLoopRc::new(None)
                .map_err(|e| EzpeekError::Capture(format!("pipewire mainloop: {e}")))?;
            let context = pw::context::ContextRc::new(&mainloop, None)
                .map_err(|e| EzpeekError::Capture(format!("pipewire context: {e}")))?;
            let core = context
                .connect_rc(None)
                .map_err(|e| EzpeekError::Capture(format!("pipewire connect: {e}")))?;
            let latest_fd: Arc<Mutex<Option<std::os::fd::OwnedFd>>> = Arc::new(Mutex::new(None));
            let mut props = pw::properties::properties! {
                *pw::keys::MEDIA_TYPE => "Video",
                *pw::keys::MEDIA_CATEGORY => "Capture",
                *pw::keys::MEDIA_ROLE => "Screen",
            };
            props.insert("target.object", _node_id.to_string());
            let stream = pw::stream::StreamBox::new(&core, "ezpeek-capture", props)
                .map_err(|e| EzpeekError::Capture(format!("pipewire stream: {e}")))?;
            let fd_slot = Arc::clone(&latest_fd);
            let _listener = stream
                .add_local_listener_with_user_data(())
                .process(move |stream, _| {
                    while let Some(mut buf) = stream.dequeue_buffer() {
                        for data in buf.datas_mut() {
                            let raw = data.fd();
                            if raw >= 0 {
                                use std::os::fd::BorrowedFd;
                                let borrowed = unsafe { BorrowedFd::borrow_raw(raw) };
                                if let Ok(dup) = dup_fd(borrowed) {
                                    *fd_slot.lock().unwrap() = Some(dup);
                                }
                            }
                        }
                    }
                })
                .register()
                .map_err(|e| EzpeekError::Capture(format!("pipewire listen: {e:?}")))?;
            std::mem::forget(_listener);
            let stream_static: pw::stream::StreamBox<'static> =
                unsafe { std::mem::transmute(stream) };
            self.inner = Some(PipeWireState {
                _mainloop: mainloop,
                _core: core,
                _stream: stream_static,
                latest_fd,
                width: Arc::new(Mutex::new(self.width)),
                height: Arc::new(Mutex::new(self.height)),
            });
            Ok(())
        }
    }
}

#[cfg(feature = "pipewire-capture")]
fn dup_fd(fd: std::os::fd::BorrowedFd<'_>) -> Result<std::os::fd::OwnedFd, EzpeekError> {
    use std::os::fd::{AsRawFd, FromRawFd};
    let duped = unsafe { libc::dup(fd.as_raw_fd()) };
    if duped < 0 {
        return Err(EzpeekError::Capture("dup dmabuf fd failed".into()));
    }
    Ok(unsafe { std::os::fd::OwnedFd::from_raw_fd(duped) })
}

impl crate::CaptureSource for PipeWireCapture {
    fn next_frame(&mut self) -> Result<GpuFrame, EzpeekError> {
        #[cfg(not(feature = "pipewire-capture"))]
        {
            Err(EzpeekError::Unsupported(
                "pipewire: rebuild with --features pipewire-capture",
            ))
        }
        #[cfg(feature = "pipewire-capture")]
        {
            let st = self.inner.as_ref().ok_or_else(|| {
                EzpeekError::Capture("pipewire: call connect_node() first".into())
            })?;
            let fd_opt = st.latest_fd.lock().unwrap().take();
            let seq = self.seq;
            self.seq += 1;
            match fd_opt {
                Some(fd) => Ok(GpuFrame {
                    handle: FrameHandle::DmaBuf(ezpeek_core::DmaBufFd::adopt(fd)),
                    width: *st.width.lock().unwrap(),
                    height: *st.height.lock().unwrap(),
                    format: PixelFormat::Nv12,
                    timestamp_ns: now_ns(),
                    seq,
                }),
                None => Err(EzpeekError::Capture("pipewire: no frame yet".into())),
            }
        }
    }
}

pub fn create() -> crate::UnsupportedCapture {
    crate::UnsupportedCapture("pipewire: use PipeWireCapture::connect_node()")
}
