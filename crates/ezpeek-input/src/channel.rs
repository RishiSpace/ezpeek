//! In-process input event channel: viewer captures local input,
//! serializes it, and the host injects it. This module is the
//! serialization + queue half; the DataChannel wire half is item 7's
//! WebRTC `input` channel. Redaction: event *types* only in logs,
//! never key codes or coordinates at default levels.

use ezpeek_core::EzpeekError;

use crate::{InputEvent, InputInjector};

pub fn input_channel() -> (InputSender, InputReceiver) {
    let (tx, rx) = std::sync::mpsc::channel::<InputEvent>();
    (InputSender { tx }, InputReceiver { rx })
}

pub struct InputSender {
    tx: std::sync::mpsc::Sender<InputEvent>,
}

impl InputSender {
    pub fn send(&self, ev: InputEvent) -> Result<(), EzpeekError> {
        self.tx
            .send(ev)
            .map_err(|_| EzpeekError::Input("input channel closed".into()))
    }

    pub fn send_wire(&self, ev: &InputEvent) -> Result<String, EzpeekError> {
        serde_json::to_string(ev).map_err(|e| EzpeekError::Input(format!("encode: {e}")))
    }
}

pub struct InputReceiver {
    rx: std::sync::mpsc::Receiver<InputEvent>,
}

impl InputReceiver {
    pub fn try_recv(&self) -> Option<InputEvent> {
        self.rx.try_recv().ok()
    }

    pub fn recv_wire(&self, raw: &str) -> Result<InputEvent, EzpeekError> {
        serde_json::from_str(raw).map_err(|e| EzpeekError::Input(format!("decode: {e}")))
    }

    pub fn drain_into(
        &self,
        injector: &mut dyn InputInjector,
        max: usize,
    ) -> Result<usize, EzpeekError> {
        let mut n = 0;
        while n < max {
            match self.rx.try_recv() {
                Ok(ev) => {
                    injector.inject(&ev)?;
                    n += 1;
                }
                Err(_) => break,
            }
        }
        Ok(n)
    }
}

pub fn event_type(ev: &InputEvent) -> &'static str {
    match ev {
        InputEvent::MouseMove { .. } => "MouseMove",
        InputEvent::MouseButton { .. } => "MouseButton",
        InputEvent::Key { .. } => "Key",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct MockInjector {
        seen: Vec<InputEvent>,
    }

    impl InputInjector for MockInjector {
        fn inject(&mut self, ev: &InputEvent) -> Result<(), EzpeekError> {
            self.seen.push(ev.clone());
            Ok(())
        }
    }

    #[test]
    fn channel_round_trips_all_types() {
        let (tx, rx) = input_channel();
        let events = vec![
            InputEvent::MouseMove { x: 10, y: -5 },
            InputEvent::MouseButton {
                button: 0,
                pressed: true,
            },
            InputEvent::Key {
                code: 30,
                pressed: false,
            },
        ];
        for ev in &events {
            tx.send(ev.clone()).unwrap();
        }
        let mut inj = MockInjector { seen: Vec::new() };
        let n = rx.drain_into(&mut inj, 16).unwrap();
        assert_eq!(n, 3);
        assert_eq!(inj.seen.len(), 3);
    }

    #[test]
    fn wire_serialization_round_trips() {
        let (tx, rx) = input_channel();
        let ev = InputEvent::Key {
            code: 42,
            pressed: true,
        };
        let raw = tx.send_wire(&ev).unwrap();
        let back = rx.recv_wire(&raw).unwrap();
        assert!(matches!(back, InputEvent::Key { code: 42, .. }));
    }

    #[test]
    fn event_type_never_leaks_content() {
        let t = event_type(&InputEvent::Key {
            code: 12345,
            pressed: true,
        });
        assert_eq!(t, "Key");
        assert!(!t.contains("12345"));
    }
}
