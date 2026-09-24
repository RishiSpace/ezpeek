use ezpeek_core::{DecodedSurface, EzpeekError};

pub trait Presenter {
    fn present(&mut self, surface: &DecodedSurface) -> Result<(), EzpeekError>;
}

#[cfg(feature = "software-present")]
pub mod software;
