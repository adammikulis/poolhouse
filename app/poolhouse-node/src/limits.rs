//! How often something may be done: a count per key within a window, kept in memory. A key names what is counted (a source
//! address, a phone, a pool). Only a use that was made is counted: `permits` asks, `record` counts.

use std::collections::{BTreeMap, VecDeque};
use std::time::{Duration, Instant};

const MOST_KEYS: usize = 1024;

#[derive(Default)]
pub struct Rates {
    hits: BTreeMap<String, VecDeque<Instant>>,
}

impl Rates {
    /// Whether one more use of ``key`` is within ``most`` uses in the last ``per``; nothing is counted.
    pub fn permits(&mut self, key: &str, most: usize, per: Duration) -> bool {
        let now = Instant::now();
        let Some(seen) = self.hits.get_mut(key) else { return most > 0 };
        while seen.front().is_some_and(|t| now.duration_since(*t) >= per) {
            seen.pop_front();
        }
        seen.len() < most
    }

    /// Count one use of ``key`` now. A new key past the bound makes room by dropping the key used least recently, so no flood of
    /// keys is refused and none keeps another from being counted.
    pub fn record(&mut self, key: &str, per: Duration) {
        let now = Instant::now();
        if self.hits.len() >= MOST_KEYS && !self.hits.contains_key(key) {
            self.hits.retain(|_, seen| seen.back().is_some_and(|t| now.duration_since(*t) < per));
            if self.hits.len() >= MOST_KEYS {
                let oldest = self.hits.iter().filter_map(|(k, seen)| seen.back().map(|t| (*t, k.clone()))).min().map(|(_, k)| k);
                oldest.into_iter().for_each(|k| drop(self.hits.remove(&k)));
            }
        }
        let seen = self.hits.entry(key.into()).or_default();
        while seen.front().is_some_and(|t| now.duration_since(*t) >= per) {
            seen.pop_front();
        }
        seen.push_back(now);
    }

    /// How many keys are kept.
    pub fn len(&self) -> usize {
        self.hits.len()
    }

    pub fn is_empty(&self) -> bool {
        self.hits.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_key_is_allowed_so_many_times_in_a_window_and_each_key_has_its_own() {
        let mut rates = Rates::default();
        let window = Duration::from_millis(80);
        for _ in 0..2 {
            assert!(rates.permits("a", 2, window));
            rates.record("a", window);
        }
        assert!(!rates.permits("a", 2, window), "a third use in the window is refused");
        assert!(!rates.permits("a", 2, window), "and asking counts nothing, so the window ends when the first two age out");
        assert!(rates.permits("b", 2, window), "another key is not held back");
        std::thread::sleep(window + Duration::from_millis(40));
        assert!(rates.permits("a", 2, window), "the window moves on");
    }

    #[test]
    fn asking_counts_nothing_and_only_a_recorded_use_does() {
        let mut rates = Rates::default();
        let window = Duration::from_secs(60);
        for _ in 0..50 {
            assert!(rates.permits("a", 1, window));
        }
        assert!(rates.is_empty(), "no use was made, so nothing is kept");
        rates.record("a", window);
        assert!(!rates.permits("a", 1, window));
    }

    #[test]
    fn the_keys_kept_are_bounded_and_a_flood_of_new_ones_drops_the_least_recently_used_instead_of_refusing_anyone() {
        let mut rates = Rates::default();
        let window = Duration::from_secs(60);
        rates.record("old", window);
        for i in 0..MOST_KEYS + 500 {
            rates.record(&format!("k{i}"), window);
        }
        assert_eq!(rates.len(), MOST_KEYS, "the table does not grow past its bound");
        assert!(rates.permits("one more", 1, window), "a key nobody used is allowed");
        rates.record("one more", window);
        assert_eq!(rates.len(), MOST_KEYS);
        assert!(!rates.permits("one more", 1, window), "and what it did is counted");
        assert!(rates.permits("old", 1, window), "the key used longest ago was the one that made room");
        assert!(!rates.permits(&format!("k{}", MOST_KEYS + 499), 1, window), "the newest keys are still held to their limit");
    }
}
