//! Linux uinput injector backend.
//! Creates a virtual mouse+keyboard via /dev/uinput.
//! Requires write access to /dev/uinput (uinput group or root).
//! Feature: `uinput`. Least privilege: only the virtual device
//! fd is held; no raw /dev/input/* access, no grab.

use ezpeek_core::EzpeekError;

use crate::{InputEvent, InputInjector};

pub struct UinputInjector {
    #[cfg(feature = "uinput")]
    device: Option<evdev::uinput::VirtualDevice>,
}

impl UinputInjector {
    pub fn new() -> Self {
        Self {
            #[cfg(feature = "uinput")]
            device: None,
        }
    }

    #[cfg(feature = "uinput")]
    fn ensure_open(&mut self) -> Result<(), EzpeekError> {
        use evdev::{AttributeSet, RelativeAxisCode};
        if self.device.is_some() {
            return Ok(());
        }
        let device = evdev::uinput::VirtualDevice::builder()
            .map_err(|e| EzpeekError::Input(format!("uinput builder: {e}")))?
            .name("ezpeek-input")
            .with_relative_axes(&AttributeSet::from_iter([
                RelativeAxisCode::REL_X,
                RelativeAxisCode::REL_Y,
            ]))
            .map_err(|e| EzpeekError::Input(format!("uinput axes: {e}")))?
            .with_keys(&evdev::AttributeSet::from_iter(
                (1u16..=248).filter_map(|c| evdev::KeyCode::new(c).into()),
            ))
            .map_err(|e| EzpeekError::Input(format!("uinput keys: {e}")))?
            .build()
            .map_err(|e| {
                EzpeekError::Input(format!("uinput build (need /dev/uinput access): {e}"))
            })?;
        self.device = Some(device);
        Ok(())
    }
}

impl Default for UinputInjector {
    fn default() -> Self {
        Self::new()
    }
}

impl InputInjector for UinputInjector {
    fn inject(&mut self, ev: &InputEvent) -> Result<(), EzpeekError> {
        #[cfg(not(feature = "uinput"))]
        {
            let _ = ev;
            Err(EzpeekError::Unsupported(
                "uinput: rebuild with --features uinput",
            ))
        }
        #[cfg(feature = "uinput")]
        {
            self.ensure_open()?;
            let device = self
                .device
                .as_mut()
                .ok_or_else(|| EzpeekError::Input("uinput not open".into()))?;
            let events: Vec<evdev::InputEvent> = match ev {
                InputEvent::MouseMove { x, y } => vec![
                    evdev::InputEvent::new(
                        evdev::EventType::RELATIVE.0,
                        evdev::RelativeAxisCode::REL_X.0,
                        *x,
                    ),
                    evdev::InputEvent::new(
                        evdev::EventType::RELATIVE.0,
                        evdev::RelativeAxisCode::REL_Y.0,
                        *y,
                    ),
                ],
                InputEvent::MouseButton { button, pressed } => {
                    let code = match button {
                        0 => evdev::KeyCode::BTN_LEFT.0,
                        1 => evdev::KeyCode::BTN_RIGHT.0,
                        _ => evdev::KeyCode::BTN_MIDDLE.0,
                    };
                    vec![evdev::InputEvent::new(
                        evdev::EventType::KEY.0,
                        code,
                        i32::from(*pressed),
                    )]
                }
                InputEvent::Key { code, pressed } => {
                    vec![evdev::InputEvent::new(
                        evdev::EventType::KEY.0,
                        *code as u16,
                        i32::from(*pressed),
                    )]
                }
            };
            device
                .emit(&events)
                .map_err(|e| EzpeekError::Input(format!("uinput emit: {e}")))?;
            Ok(())
        }
    }
}
