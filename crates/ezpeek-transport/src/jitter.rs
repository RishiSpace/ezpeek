use std::collections::VecDeque;

use ezpeek_core::EzpeekError;

pub struct JitterBuffer {
    queue: VecDeque<JitterPacket>,
    capacity: usize,
    next_seq: Option<u64>,
}

#[derive(Debug, Clone)]
pub struct JitterPacket {
    pub seq: u64,
    pub timestamp_ns: u64,
    pub payload: Vec<u8>,
    pub is_keyframe: bool,
}

impl JitterBuffer {
    pub fn new(capacity: usize) -> Self {
        Self {
            queue: VecDeque::with_capacity(capacity),
            capacity,
            next_seq: None,
        }
    }

    pub fn push(&mut self, pkt: JitterPacket) -> Result<(), EzpeekError> {
        if self.queue.len() >= self.capacity {
            self.queue.pop_front();
        }
        let pos = self
            .queue
            .iter()
            .position(|p| p.seq > pkt.seq)
            .unwrap_or(self.queue.len());
        self.queue.insert(pos, pkt);
        Ok(())
    }

    pub fn pop_in_order(&mut self) -> Option<JitterPacket> {
        let want = self.next_seq?;
        if self.queue.front().map(|p| p.seq) == Some(want) {
            let pkt = self.queue.pop_front()?;
            self.next_seq = Some(want + 1);
            return Some(pkt);
        }
        if self.queue.front().map(|p| p.seq).unwrap_or(u64::MAX) > want {
            self.next_seq = self.queue.front().map(|p| p.seq);
            return self.pop_in_order();
        }
        None
    }

    pub fn pop_any(&mut self) -> Option<JitterPacket> {
        let pkt = self.queue.pop_front()?;
        if self.next_seq.is_none() {
            self.next_seq = Some(pkt.seq + 1);
        }
        Some(pkt)
    }

    pub fn len(&self) -> usize {
        self.queue.len()
    }

    pub fn is_empty(&self) -> bool {
        self.queue.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pkt(seq: u64) -> JitterPacket {
        JitterPacket {
            seq,
            timestamp_ns: seq * 1_000_000,
            payload: vec![seq as u8],
            is_keyframe: seq == 0,
        }
    }

    #[test]
    fn reorders_out_of_order() {
        let mut jb = JitterBuffer::new(16);
        jb.push(pkt(2)).unwrap();
        jb.push(pkt(0)).unwrap();
        jb.push(pkt(1)).unwrap();
        jb.next_seq = Some(0);
        assert_eq!(jb.pop_in_order().unwrap().seq, 0);
        assert_eq!(jb.pop_in_order().unwrap().seq, 1);
        assert_eq!(jb.pop_in_order().unwrap().seq, 2);
        assert!(jb.pop_in_order().is_none());
    }

    #[test]
    fn skips_gap_on_advance() {
        let mut jb = JitterBuffer::new(16);
        jb.push(pkt(5)).unwrap();
        jb.next_seq = Some(3);
        assert_eq!(jb.pop_in_order().unwrap().seq, 5);
    }

    #[test]
    fn drops_oldest_when_full() {
        let mut jb = JitterBuffer::new(2);
        jb.push(pkt(0)).unwrap();
        jb.push(pkt(1)).unwrap();
        jb.push(pkt(2)).unwrap();
        assert_eq!(jb.len(), 2);
        assert_eq!(jb.pop_any().unwrap().seq, 1);
    }
}
