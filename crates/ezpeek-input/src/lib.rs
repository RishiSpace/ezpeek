use ezpeek_core::EzpeekError;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum InputEvent {
    MouseMove { x: i32, y: i32 },
    MouseButton { button: u8, pressed: bool },
    Key { code: u32, pressed: bool },
}

pub trait InputInjector {
    fn inject(&mut self, ev: &InputEvent) -> Result<(), EzpeekError>;
}

#[cfg(feature = "uinput")]
pub mod uinput;

#[cfg(feature = "uinput")]
pub use uinput::UinputInjector;

pub mod channel;

pub use channel::{input_channel, InputReceiver, InputSender};
