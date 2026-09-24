use std::collections::VecDeque;

use crate::error::EzpeekError;

pub struct FramePool<T> {
    free: VecDeque<T>,
    capacity: usize,
}

impl<T> FramePool<T> {
    pub fn new(factory: impl Fn() -> T, capacity: usize) -> Self {
        let mut free = VecDeque::with_capacity(capacity);
        for _ in 0..capacity {
            free.push_back(factory());
        }
        Self { free, capacity }
    }

    pub fn acquire(&mut self) -> Result<T, EzpeekError> {
        self.free.pop_front().ok_or(EzpeekError::PoolExhausted)
    }

    pub fn release(&mut self, item: T) {
        if self.free.len() < self.capacity {
            self.free.push_back(item);
        }
    }

    pub fn len(&self) -> usize {
        self.free.len()
    }

    pub fn is_empty(&self) -> bool {
        self.free.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pool_reuses_released_items() {
        let mut pool = FramePool::new(|| 42u32, 2);
        let a = pool.acquire().unwrap();
        let b = pool.acquire().unwrap();
        assert!(pool.acquire().is_err());
        pool.release(a);
        pool.release(b);
        assert_eq!(pool.len(), 2);
    }
}
