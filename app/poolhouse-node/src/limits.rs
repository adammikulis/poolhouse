//! How often something may be done: a count per key within a window, kept in memory.

use std::collections::{BTreeMap, VecDeque};
use std::time::{Duration, Instant};

const MOST_KEYS: usize = 1024;

#[derive(Default)]
pub struct Rates {
    hits: BTreeMap<String, VecDeque<Instant>>,
}

impl Rates {
    /// Count one use of ``key``: true when it is within ``most`` uses in the last ``per``, false (and not counted) when it is not.
    pub fn allow(&mut self, key: &str, most: usize, per: Duration) -> bool {
        let now = Instant::now();
        if self.hits.len() >= MOST_KEYS && !self.hits.contains_key(key) {
            self.hits.retain(|_, seen| seen.back().is_some_and(|t| now.duration_since(*t) < per));
            if self.hits.len() >= MOST_KEYS {
                return false;
            }
        }
        let seen = self.hits.entry(key.into()).or_default();
        while seen.front().is_some_and(|t| now.duration_since(*t) >= per) {
            seen.pop_front();
        }
        if seen.len() >= most {
            return false;
        }
        seen.push_back(now);
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_key_is_allowed_so_many_times_in_a_window_and_each_key_has_its_own() {
        let mut rates = Rates::default();
        let window = Duration::from_millis(80);
        assert!(rates.allow("a", 2, window) && rates.allow("a", 2, window));
        assert!(!rates.allow("a", 2, window), "a third use in the window is refused");
        assert!(!rates.allow("a", 2, window), "and a refused use is not counted, so the window ends when the first two age out");
        assert!(rates.allow("b", 2, window), "another key is not held back");
        std::thread::sleep(window + Duration::from_millis(40));
        assert!(rates.allow("a", 2, window), "the window moves on");
    }

    #[test]
    fn the_keys_kept_are_bounded() {
        let mut rates = Rates::default();
        let window = Duration::from_secs(60);
        for i in 0..MOST_KEYS {
            assert!(rates.allow(&format!("k{i}"), 1, window));
        }
        assert!(!rates.allow("one more", 1, window), "a new key past the bound is refused while every old one is still in its window");
        assert!(rates.allow("k0", 5, window), "a key already kept still counts");
    }
}
