use std::collections::BTreeMap;
use std::time::{Duration, Instant, SystemTime};

use serde::de::{self, MapAccess, Visitor};
use serde::{Deserialize, Deserializer};
use sha2::{Digest, Sha256};

use super::{AttestationError, Result};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Status {
    status: State,
    expires: Option<String>,
    reason: Option<Reason>,
    comment: Option<String>,
}

#[derive(Deserialize)]
enum State {
    REVOKED,
    SUSPENDED,
}

#[derive(Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
enum Reason {
    Unspecified,
    KeyCompromise,
    CaCompromise,
    Superseded,
    SoftwareFlaw,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Document {
    #[serde(deserialize_with = "entries")]
    entries: BTreeMap<String, Status>,
}

fn entries<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<BTreeMap<String, Status>, D::Error> {
    struct Unique;
    impl<'de> Visitor<'de> for Unique {
        type Value = BTreeMap<String, Status>;
        fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
            f.write_str("unique certificate status entries")
        }
        fn visit_map<M: MapAccess<'de>>(
            self,
            mut map: M,
        ) -> std::result::Result<Self::Value, M::Error> {
            let mut entries = BTreeMap::new();
            while let Some((key, value)) = map.next_entry::<String, Status>()? {
                if entries.len() >= 100_000
                    || key.is_empty()
                    || key.len() > 40
                    || key.starts_with('0')
                    || !key
                        .bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                {
                    return Err(de::Error::custom("invalid certificate serial"));
                }
                if entries.insert(key, value).is_some() {
                    return Err(de::Error::custom("duplicate certificate serial"));
                }
            }
            Ok(entries)
        }
    }
    deserializer.deserialize_map(Unique)
}

/// A bounded, in-memory official HTTPS revocation response.
pub struct FreshRevocations {
    entries: BTreeMap<String, Status>,
    received: Instant,
    wall: SystemTime,
    lifetime: Duration,
    digest: [u8; 32],
}

impl FreshRevocations {
    #[cfg(test)]
    pub(super) fn received_for_test(&mut self, ago: Duration) {
        self.received = Instant::now() - ago;
    }

    pub(in crate::phone) fn from_official_https(
        body: &[u8],
        fetched_at: SystemTime,
        max_age: Duration,
        age: Duration,
    ) -> Result<Self> {
        if body.len() > 2 * 1024 * 1024 {
            return Err(AttestationError::Bounds);
        }
        let lifetime = max_age
            .checked_sub(age)
            .filter(|d| !d.is_zero())
            .ok_or(AttestationError::Revocations)?;
        if fetched_at > SystemTime::now() || max_age > Duration::from_secs(86_400) {
            return Err(AttestationError::Revocations);
        }
        let doc: Document =
            serde_json::from_slice(body).map_err(|_| AttestationError::Revocations)?;
        for entry in doc.entries.values() {
            let _ = (&entry.status, &entry.reason);
            if entry
                .comment
                .as_ref()
                .is_some_and(|c| c.chars().count() > 140)
                || entry.expires.as_ref().is_some_and(|s| !date(s))
            {
                return Err(AttestationError::Revocations);
            }
        }
        Ok(Self {
            entries: doc.entries,
            received: Instant::now(),
            wall: fetched_at,
            lifetime,
            digest: Sha256::digest(body).into(),
        })
    }

    pub(super) fn evidence(&self) -> ([u8; 32], SystemTime, SystemTime) {
        (self.digest, self.wall, self.wall + self.lifetime)
    }

    pub(super) fn current(&self, now: SystemTime) -> Result<()> {
        if self.received.elapsed() >= self.lifetime
            || now
                .duration_since(self.wall)
                .map_or(true, |d| d >= self.lifetime)
        {
            return Err(AttestationError::Revocations);
        }
        Ok(())
    }

    pub(super) fn check(&self, serial: &[u8]) -> Result<()> {
        let serial = serial
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>();
        let serial = serial.trim_start_matches('0');
        if serial.is_empty() || self.entries.contains_key(serial) {
            return Err(AttestationError::Revoked);
        }
        Ok(())
    }
}

fn date(s: &str) -> bool {
    let b = s.as_bytes();
    if b.len() != 10
        || b[4] != b'-'
        || b[7] != b'-'
        || !b
            .iter()
            .enumerate()
            .all(|(i, b)| i == 4 || i == 7 || b.is_ascii_digit())
    {
        return false;
    }
    let year = s[..4].parse::<u32>().unwrap_or(0);
    let month = s[5..7].parse::<u32>().unwrap_or(0);
    let day = s[8..].parse::<u32>().unwrap_or(0);
    let days = match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => {
            if year % 4 == 0 && (year % 100 != 0 || year % 400 == 0) {
                29
            } else {
                28
            }
        }
        _ => 0,
    };
    year > 0 && day > 0 && day <= days
}
