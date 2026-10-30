//! Canonical source-pack data. This module is not an authorization or custody witness.
//! It preserves exact UTF-8 path bytes and does not establish destination filesystem
//! name equivalence; protected admission and the receiver must validate that separately.

use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::io::{Read, Seek, SeekFrom, Write};

const DOMAIN: &[u8] = b"poolhouse.source-manifest.v1\0";
pub const PACK_DOMAIN: &[u8] = b"poolhouse.source-pack.v1\0";
const HARD_ENTRIES: u32 = 100_000;
const HARD_ENCODED: usize = 32 * 1024 * 1024;
const PATH_LIMIT: usize = 4096;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct InvalidManifest;

/// Limits are data, not authorization. The protected owner must supply policy-bound
/// byte, storage and time budgets; this codec has no implicit file or pack ceiling.
#[derive(Clone, Copy, Debug)]
pub struct Limits {
    pub max_entries: u32,
    pub max_file_size: u64,
    pub max_pack_size: u64,
    pub max_encoded_size: usize,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum EntryKind {
    Directory,
    File {
        offset: u64,
        size: u64,
        digest: [u8; 32],
        executable: bool,
    },
    Absent,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Entry {
    pub path: Vec<u8>,
    pub kind: EntryKind,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Manifest {
    pub entries: Vec<Entry>,
    pub pack_size: u64,
    pub pack_digest: [u8; 32],
}

fn checked_limits(mut limits: Limits) -> Result<Limits, InvalidManifest> {
    if limits.max_entries == 0
        || limits.max_file_size == 0
        || limits.max_pack_size == 0
        || limits.max_encoded_size == 0
    {
        return Err(InvalidManifest);
    }
    limits.max_entries = limits.max_entries.min(HARD_ENTRIES);
    limits.max_encoded_size = limits.max_encoded_size.min(HARD_ENCODED);
    Ok(limits)
}

fn valid_path(path: &[u8]) -> bool {
    if path.is_empty() || path.len() > PATH_LIMIT {
        return false;
    }
    let Ok(value) = std::str::from_utf8(path) else {
        return false;
    };
    !value.chars().any(|c| c.is_control() || c == '\\')
        && value
            .split('/')
            .all(|part| !part.is_empty() && part != "." && part != "..")
}

impl Manifest {
    fn validate(&self, limits: Limits) -> Result<usize, InvalidManifest> {
        let mut state = Validation::new(limits)?;
        for entry in &self.entries {
            state.push(entry)?;
        }
        if state.offset != self.pack_size {
            return Err(InvalidManifest);
        }
        Ok(state.encoded)
    }

    pub fn encode(&self, limits: Limits) -> Result<Vec<u8>, InvalidManifest> {
        let size = self.validate(limits)?;
        let mut out = Vec::new();
        out.try_reserve_exact(size).map_err(|_| InvalidManifest)?;
        out.extend_from_slice(DOMAIN);
        out.extend_from_slice(&(self.entries.len() as u32).to_be_bytes());
        out.extend_from_slice(&self.pack_size.to_be_bytes());
        out.extend_from_slice(&self.pack_digest);
        for entry in &self.entries {
            out.extend_from_slice(&(entry.path.len() as u32).to_be_bytes());
            out.extend_from_slice(&entry.path);
            match entry.kind {
                EntryKind::Directory => out.push(1),
                EntryKind::Absent => out.push(3),
                EntryKind::File {
                    offset,
                    size,
                    digest,
                    executable,
                } => {
                    out.push(2);
                    out.extend_from_slice(&offset.to_be_bytes());
                    out.extend_from_slice(&size.to_be_bytes());
                    out.extend_from_slice(&digest);
                    out.push(u8::from(executable));
                }
            }
        }
        Ok(out)
    }

    /// Hash the exact domain-tagged canonical encoding; this is not authorization.
    pub fn digest(&self, limits: Limits) -> Result<[u8; 32], InvalidManifest> {
        Ok(Sha256::digest(self.encode(limits)?).into())
    }

    pub fn decode(bytes: &[u8], limits: Limits) -> Result<Self, InvalidManifest> {
        let limits = checked_limits(limits)?;
        if bytes.len() > limits.max_encoded_size {
            return Err(InvalidManifest);
        }
        let mut cursor = Cursor { bytes, offset: 0 };
        if cursor.take(DOMAIN.len())? != DOMAIN {
            return Err(InvalidManifest);
        }
        let count = cursor.u32()?;
        if count > limits.max_entries {
            return Err(InvalidManifest);
        }
        let pack_size = cursor.u64()?;
        let pack_digest = cursor.hash()?;
        if count as usize > (bytes.len() - cursor.offset) / 6 {
            return Err(InvalidManifest);
        }
        let mut entries = Vec::new();
        entries
            .try_reserve_exact(count as usize)
            .map_err(|_| InvalidManifest)?;
        for _ in 0..count {
            let length = cursor.u32()? as usize;
            if length > PATH_LIMIT {
                return Err(InvalidManifest);
            }
            let path = cursor.take(length)?.to_vec();
            let kind = match cursor.byte()? {
                1 => EntryKind::Directory,
                3 => EntryKind::Absent,
                2 => {
                    let offset = cursor.u64()?;
                    let size = cursor.u64()?;
                    let digest = cursor.hash()?;
                    let executable = match cursor.byte()? {
                        0 => false,
                        1 => true,
                        _ => return Err(InvalidManifest),
                    };
                    EntryKind::File {
                        offset,
                        size,
                        digest,
                        executable,
                    }
                }
                _ => return Err(InvalidManifest),
            };
            entries.push(Entry { path, kind });
        }
        if cursor.offset != bytes.len() {
            return Err(InvalidManifest);
        }
        let manifest = Self {
            entries,
            pack_size,
            pack_digest,
        };
        if manifest.encode(limits)?.as_slice() != bytes {
            return Err(InvalidManifest);
        }
        Ok(manifest)
    }

    /// Check exact bytes from the supplied object. This does not establish custody or authorization.
    pub fn verify_pack<R: Read + Seek>(
        &self,
        input: &mut R,
        limits: Limits,
    ) -> Result<(), InvalidManifest> {
        self.validate(limits)?;
        input
            .seek(SeekFrom::Start(0))
            .map_err(|_| InvalidManifest)?;
        let mut whole = Sha256::new();
        whole.update(PACK_DOMAIN);
        let mut buffer = [0u8; 65536];
        for entry in &self.entries {
            if let EntryKind::File { size, digest, .. } = entry.kind {
                let mut file = Sha256::new();
                let mut remaining = size;
                while remaining != 0 {
                    let count = remaining.min(buffer.len() as u64) as usize;
                    input
                        .read_exact(&mut buffer[..count])
                        .map_err(|_| InvalidManifest)?;
                    file.update(&buffer[..count]);
                    whole.update(&buffer[..count]);
                    remaining -= count as u64;
                }
                if <[u8; 32]>::from(file.finalize()) != digest {
                    return Err(InvalidManifest);
                }
            }
        }
        if input.read(&mut buffer[..1]).map_err(|_| InvalidManifest)? != 0
            || <[u8; 32]>::from(whole.finalize()) != self.pack_digest
        {
            return Err(InvalidManifest);
        }
        Ok(())
    }
}

struct Validation {
    limits: Limits,
    parents: BTreeMap<Vec<u8>, bool>,
    previous: Option<Vec<u8>>,
    count: u32,
    offset: u64,
    encoded: usize,
}
impl Validation {
    fn new(limits: Limits) -> Result<Self, InvalidManifest> {
        let limits = checked_limits(limits)?;
        let encoded = DOMAIN
            .len()
            .checked_add(4 + 8 + 32)
            .ok_or(InvalidManifest)?;
        if encoded > limits.max_encoded_size {
            return Err(InvalidManifest);
        }
        Ok(Self {
            limits,
            parents: BTreeMap::new(),
            previous: None,
            count: 0,
            offset: 0,
            encoded,
        })
    }
    fn push(&mut self, entry: &Entry) -> Result<(), InvalidManifest> {
        if !valid_path(&entry.path)
            || self
                .previous
                .as_ref()
                .is_some_and(|last| last >= &entry.path)
        {
            return Err(InvalidManifest);
        }
        if let Some(split) = entry.path.iter().rposition(|byte| *byte == b'/') {
            match self.parents.get(&entry.path[..split]) {
                Some(true) => (),
                Some(false) if matches!(entry.kind, EntryKind::Absent) => (),
                _ => return Err(InvalidManifest),
            }
        }
        let count = self.count.checked_add(1).ok_or(InvalidManifest)?;
        let mut encoded = self
            .encoded
            .checked_add(4 + entry.path.len() + 1)
            .ok_or(InvalidManifest)?;
        let mut offset = self.offset;
        match entry.kind {
            EntryKind::Directory | EntryKind::Absent => (),
            EntryKind::File {
                offset: start,
                size,
                ..
            } => {
                if start != offset || size > self.limits.max_file_size {
                    return Err(InvalidManifest);
                }
                offset = offset.checked_add(size).ok_or(InvalidManifest)?;
                encoded = encoded.checked_add(8 + 8 + 32 + 1).ok_or(InvalidManifest)?;
            }
        }
        if count > self.limits.max_entries
            || offset > self.limits.max_pack_size
            || encoded > self.limits.max_encoded_size
        {
            return Err(InvalidManifest);
        }
        match entry.kind {
            EntryKind::Directory => {
                self.parents.insert(entry.path.clone(), true);
            }
            EntryKind::Absent => {
                self.parents.insert(entry.path.clone(), false);
            }
            EntryKind::File { .. } => (),
        }
        self.previous = Some(entry.path.clone());
        self.count = count;
        self.offset = offset;
        self.encoded = encoded;
        Ok(())
    }
}

/// Builds data from selected streams into one supplied object. It is not a protected source witness.
pub struct PackBuilder<W> {
    output: W,
    state: Validation,
    entries: Vec<Entry>,
    whole: Sha256,
    failed: bool,
}
impl<W: Read + Write + Seek> PackBuilder<W> {
    pub fn new(mut output: W, limits: Limits) -> Result<Self, InvalidManifest> {
        let state = Validation::new(limits)?;
        if output.seek(SeekFrom::End(0)).map_err(|_| InvalidManifest)? != 0 {
            return Err(InvalidManifest);
        }
        output
            .seek(SeekFrom::Start(0))
            .map_err(|_| InvalidManifest)?;
        let mut whole = Sha256::new();
        whole.update(PACK_DOMAIN);
        Ok(Self {
            output,
            state,
            entries: Vec::new(),
            whole,
            failed: false,
        })
    }
    fn entry(&mut self, entry: Entry) -> Result<(), InvalidManifest> {
        if self.failed {
            return Err(InvalidManifest);
        }
        if self.state.push(&entry).is_err() {
            self.failed = true;
            return Err(InvalidManifest);
        }
        self.entries.push(entry);
        Ok(())
    }
    pub fn directory(&mut self, path: Vec<u8>) -> Result<(), InvalidManifest> {
        self.entry(Entry {
            path,
            kind: EntryKind::Directory,
        })
    }
    pub fn absent(&mut self, path: Vec<u8>) -> Result<(), InvalidManifest> {
        self.entry(Entry {
            path,
            kind: EntryKind::Absent,
        })
    }
    pub fn file<R: Read>(
        &mut self,
        path: Vec<u8>,
        size: u64,
        executable: bool,
        input: &mut R,
    ) -> Result<(), InvalidManifest> {
        if self.failed {
            return Err(InvalidManifest);
        }
        let offset = self.state.offset;
        let mut entry = Entry {
            path,
            kind: EntryKind::File {
                offset,
                size,
                digest: [0; 32],
                executable,
            },
        };
        if self.state.push(&entry).is_err() {
            self.failed = true;
            return Err(InvalidManifest);
        }
        self.failed = true;
        let mut hash = Sha256::new();
        let mut remaining = size;
        let mut buffer = [0u8; 65536];
        while remaining != 0 {
            let count = remaining.min(buffer.len() as u64) as usize;
            input
                .read_exact(&mut buffer[..count])
                .map_err(|_| InvalidManifest)?;
            self.output
                .write_all(&buffer[..count])
                .map_err(|_| InvalidManifest)?;
            hash.update(&buffer[..count]);
            self.whole.update(&buffer[..count]);
            remaining -= count as u64;
        }
        if input.read(&mut buffer[..1]).map_err(|_| InvalidManifest)? != 0 {
            return Err(InvalidManifest);
        }
        if let EntryKind::File { digest, .. } = &mut entry.kind {
            *digest = hash.finalize().into();
        }
        self.entries.push(entry);
        self.failed = false;
        Ok(())
    }
    pub fn finish(mut self) -> Result<(W, Manifest), InvalidManifest> {
        if self.failed {
            return Err(InvalidManifest);
        }
        self.output.flush().map_err(|_| InvalidManifest)?;
        let manifest = Manifest {
            entries: self.entries,
            pack_size: self.state.offset,
            pack_digest: self.whole.finalize().into(),
        };
        manifest.verify_pack(&mut self.output, self.state.limits)?;
        Ok((self.output, manifest))
    }
}

struct Cursor<'a> {
    bytes: &'a [u8],
    offset: usize,
}
impl<'a> Cursor<'a> {
    fn take(&mut self, length: usize) -> Result<&'a [u8], InvalidManifest> {
        let end = self.offset.checked_add(length).ok_or(InvalidManifest)?;
        let value = self.bytes.get(self.offset..end).ok_or(InvalidManifest)?;
        self.offset = end;
        Ok(value)
    }
    fn byte(&mut self) -> Result<u8, InvalidManifest> {
        Ok(self.take(1)?[0])
    }
    fn u32(&mut self) -> Result<u32, InvalidManifest> {
        Ok(u32::from_be_bytes(
            self.take(4)?.try_into().map_err(|_| InvalidManifest)?,
        ))
    }
    fn u64(&mut self) -> Result<u64, InvalidManifest> {
        Ok(u64::from_be_bytes(
            self.take(8)?.try_into().map_err(|_| InvalidManifest)?,
        ))
    }
    fn hash(&mut self) -> Result<[u8; 32], InvalidManifest> {
        self.take(32)?.try_into().map_err(|_| InvalidManifest)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use sha2::{Digest, Sha256};
    use std::io::Cursor;

    fn test_limits() -> Limits {
        Limits {
            max_entries: 100_000,
            max_file_size: 16 * 1024 * 1024,
            max_pack_size: 256 * 1024 * 1024,
            max_encoded_size: 32 * 1024 * 1024,
        }
    }

    fn digest(bytes: &[u8]) -> [u8; 32] {
        Sha256::digest(bytes).into()
    }

    fn sample() -> Manifest {
        let mut whole = Sha256::new();
        whole.update(PACK_DOMAIN);
        whole.update(b"abc");
        Manifest {
            entries: vec![
                Entry {
                    path: b"dir".to_vec(),
                    kind: EntryKind::Directory,
                },
                Entry {
                    path: b"dir/a".to_vec(),
                    kind: EntryKind::File {
                        offset: 0,
                        size: 3,
                        digest: digest(b"abc"),
                        executable: true,
                    },
                },
                Entry {
                    path: b"dir/empty".to_vec(),
                    kind: EntryKind::File {
                        offset: 3,
                        size: 0,
                        digest: digest(b""),
                        executable: false,
                    },
                },
                Entry {
                    path: b"dir/missing".to_vec(),
                    kind: EntryKind::Absent,
                },
            ],
            pack_size: 3,
            pack_digest: whole.finalize().into(),
        }
    }

    #[test]
    fn exact_round_trip_and_held_pack_bytes() {
        let manifest = sample();
        let limits = test_limits();
        let encoded = manifest.encode(limits).unwrap();
        assert_eq!(Manifest::decode(&encoded, limits).unwrap(), manifest);
        manifest
            .verify_pack(&mut Cursor::new(b"abc"), limits)
            .unwrap();
        assert!(manifest
            .verify_pack(&mut Cursor::new(b"abd"), limits)
            .is_err());
        assert!(manifest
            .verify_pack(&mut Cursor::new(b"abc!"), limits)
            .is_err());
        assert!(manifest
            .verify_pack(&mut Cursor::new(b"ab"), limits)
            .is_err());
    }

    #[test]
    fn rejects_duplicate_paths_bad_parents_ranges_and_unsafe_names() {
        let limits = test_limits();
        for mutate in 0..7 {
            let mut value = sample();
            match mutate {
                0 => value.entries.push(value.entries[0].clone()),
                1 => {
                    value.entries.remove(0);
                }
                2 => {
                    if let EntryKind::File { offset, .. } = &mut value.entries[1].kind {
                        *offset = 1
                    }
                }
                3 => value.pack_size = 4,
                4 => value.entries[1].path = b"../outside".to_vec(),
                5 => value.entries[0].kind = EntryKind::Absent,
                6 => value.entries[1].path = b"dir/invalid\0name".to_vec(),
                _ => unreachable!(),
            };
            assert!(value.encode(limits).is_err(), "mutation {mutate}");
        }
    }

    #[test]
    fn rejects_trailing_bytes_and_low_resource_limits() {
        let value = sample();
        let limits = test_limits();
        let mut encoded = value.encode(limits).unwrap();
        encoded.push(0);
        assert!(Manifest::decode(&encoded, limits).is_err());
        let mut impossible = value.encode(limits).unwrap();
        impossible[DOMAIN.len()..DOMAIN.len() + 4]
            .copy_from_slice(&limits.max_entries.to_be_bytes());
        assert!(Manifest::decode(&impossible, limits).is_err());
        assert!(value
            .encode(Limits {
                max_entries: 3,
                ..limits
            })
            .is_err());
        assert!(value
            .encode(Limits {
                max_file_size: 2,
                ..limits
            })
            .is_err());
        assert!(value
            .encode(Limits {
                max_pack_size: 2,
                ..limits
            })
            .is_err());
        assert!(value
            .encode(Limits {
                max_file_size: 0,
                ..limits
            })
            .is_err());
        let wider = Limits {
            max_entries: HARD_ENTRIES + 1,
            max_encoded_size: HARD_ENCODED + 1,
            ..limits
        };
        let mut too_many = value.encode(limits).unwrap();
        too_many[DOMAIN.len()..DOMAIN.len() + 4].copy_from_slice(&(HARD_ENTRIES + 1).to_be_bytes());
        assert!(Manifest::decode(&too_many, wider).is_err());
        assert!(Manifest::decode(&vec![0; HARD_ENCODED + 1], wider).is_err());
    }

    #[test]
    fn producer_writes_one_pack_from_selected_streams_and_rechecks_it() {
        let limits = test_limits();
        let mut builder = PackBuilder::new(Cursor::new(Vec::new()), limits).unwrap();
        builder.directory(b"dir".to_vec()).unwrap();
        builder
            .file(b"dir/a".to_vec(), 3, true, &mut Cursor::new(b"abc"))
            .unwrap();
        builder
            .file(b"dir/empty".to_vec(), 0, false, &mut Cursor::new(b""))
            .unwrap();
        builder.absent(b"dir/missing".to_vec()).unwrap();
        let (mut pack, manifest) = builder.finish().unwrap();
        assert_eq!(manifest, sample());
        assert_eq!(pack.get_ref(), b"abc");
        manifest.verify_pack(&mut pack, limits).unwrap();
    }

    #[test]
    fn producer_fences_partial_or_invalid_input() {
        let limits = test_limits();
        let mut builder = PackBuilder::new(Cursor::new(Vec::new()), limits).unwrap();
        assert!(builder
            .file(b"file".to_vec(), 3, false, &mut Cursor::new(b"ab"))
            .is_err());
        assert!(builder.finish().is_err());
        let mut builder = PackBuilder::new(Cursor::new(Vec::new()), limits).unwrap();
        assert!(builder
            .file(b"../secret".to_vec(), 1, false, &mut Cursor::new(b"x"))
            .is_err());
        assert!(builder.finish().is_err());
    }

    #[test]
    fn every_truncated_manifest_and_unknown_tag_is_rejected() {
        let limits = test_limits();
        let encoded = sample().encode(limits).unwrap();
        for end in 0..encoded.len() {
            assert!(
                Manifest::decode(&encoded[..end], limits).is_err(),
                "truncation {end}"
            );
        }
        let mut bad = encoded;
        let first_tag = DOMAIN.len() + 4 + 8 + 32 + 4 + b"dir".len();
        bad[first_tag] = 99;
        assert!(Manifest::decode(&bad, limits).is_err());
    }

    #[test]
    fn producer_detects_pack_mutation_before_finish() {
        let mut builder = PackBuilder::new(Cursor::new(Vec::new()), test_limits()).unwrap();
        builder
            .file(b"file".to_vec(), 3, false, &mut Cursor::new(b"abc"))
            .unwrap();
        builder.output.get_mut()[0] = b'z';
        assert!(builder.finish().is_err());
    }

    #[test]
    fn invalid_selection_is_rejected_before_reading_input() {
        struct Probe(bool);
        impl Read for Probe {
            fn read(&mut self, _buffer: &mut [u8]) -> std::io::Result<usize> {
                self.0 = true;
                Ok(0)
            }
        }
        let mut builder = PackBuilder::new(Cursor::new(Vec::new()), test_limits()).unwrap();
        let mut probe = Probe(false);
        assert!(builder
            .file(b"../private".to_vec(), 1, false, &mut probe)
            .is_err());
        assert!(!probe.0);
    }

    #[test]
    fn absent_fixture_can_have_an_explicit_absent_parent() {
        let mut builder = PackBuilder::new(Cursor::new(Vec::new()), test_limits()).unwrap();
        builder.absent(b"missing".to_vec()).unwrap();
        builder.absent(b"missing/nested".to_vec()).unwrap();
        builder.absent(b"missing/nested/fixture".to_vec()).unwrap();
        let (mut pack, manifest) = builder.finish().unwrap();
        let encoded = manifest.encode(test_limits()).unwrap();
        assert_eq!(Manifest::decode(&encoded, test_limits()).unwrap(), manifest);
        manifest.verify_pack(&mut pack, test_limits()).unwrap();
        let mut bad = manifest;
        bad.entries[1].kind = EntryKind::Directory;
        assert!(bad.encode(test_limits()).is_err());
        bad.entries[1].kind = EntryKind::File {
            offset: 0,
            size: 0,
            digest: digest(b""),
            executable: false,
        };
        assert!(bad.encode(test_limits()).is_err());
    }

    #[test]
    fn policy_can_allow_streamed_pack_sizes_above_legacy_snapshot_limits() {
        let size = 300 * 1024 * 1024;
        let limits = Limits {
            max_entries: 10,
            max_file_size: size,
            max_pack_size: size,
            max_encoded_size: 4096,
        };
        let manifest = Manifest {
            entries: vec![Entry {
                path: b"large".to_vec(),
                kind: EntryKind::File {
                    offset: 0,
                    size,
                    digest: [7; 32],
                    executable: false,
                },
            }],
            pack_size: size,
            pack_digest: [8; 32],
        };
        let encoded = manifest.encode(limits).unwrap();
        assert_eq!(Manifest::decode(&encoded, limits).unwrap(), manifest);
    }

    #[test]
    fn partial_pack_write_fences_the_builder() {
        struct Broken(Cursor<Vec<u8>>);
        impl Read for Broken {
            fn read(&mut self, bytes: &mut [u8]) -> std::io::Result<usize> {
                self.0.read(bytes)
            }
        }
        impl Seek for Broken {
            fn seek(&mut self, position: SeekFrom) -> std::io::Result<u64> {
                self.0.seek(position)
            }
        }
        impl Write for Broken {
            fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
                if self.0.position() == 0 {
                    self.0.write(&bytes[..bytes.len().min(1)])
                } else {
                    Err(std::io::Error::other("synthetic write failure"))
                }
            }
            fn flush(&mut self) -> std::io::Result<()> {
                Ok(())
            }
        }
        let output = Broken(Cursor::new(Vec::new()));
        let mut builder = PackBuilder::new(output, test_limits()).unwrap();
        assert!(builder
            .file(b"file".to_vec(), 3, false, &mut Cursor::new(b"abc"))
            .is_err());
        assert!(builder.finish().is_err());
    }

    #[test]
    fn held_pack_keeps_selected_bytes_after_source_path_replacement() {
        struct Temporary(std::path::PathBuf);
        impl Drop for Temporary {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let directory =
            std::env::temp_dir().join(format!("poolhouse-pack-{}-{unique}", std::process::id()));
        std::fs::create_dir(&directory).unwrap();
        let _owned = Temporary(directory.clone());
        let source = directory.join("source");
        std::fs::write(&source, b"selected").unwrap();
        let mut input = std::fs::File::open(&source).unwrap();
        let output = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(directory.join("pack"))
            .unwrap();
        let mut builder = PackBuilder::new(output, test_limits()).unwrap();
        builder
            .file(b"source".to_vec(), 8, false, &mut input)
            .unwrap();
        drop(input);
        std::fs::rename(&source, directory.join("moved")).unwrap();
        std::fs::write(&source, b"replacement").unwrap();
        let (mut pack, manifest) = builder.finish().unwrap();
        manifest.verify_pack(&mut pack, test_limits()).unwrap();
        pack.seek(SeekFrom::Start(0)).unwrap();
        let mut result = Vec::new();
        pack.read_to_end(&mut result).unwrap();
        assert_eq!(result, b"selected");
    }

    #[test]
    fn wider_policy_does_not_reject_a_small_manifest_or_change_its_bytes() {
        let value = sample();
        let ordinary = test_limits();
        let wider = Limits {
            max_entries: HARD_ENTRIES + 1,
            max_encoded_size: HARD_ENCODED + 1,
            ..ordinary
        };
        let expected = value.encode(ordinary).unwrap();
        assert_eq!(value.encode(wider).unwrap(), expected);
        assert_eq!(Manifest::decode(&expected, wider).unwrap(), value);
    }

    #[test]
    fn manifest_digest_is_sha256_of_exact_domain_tagged_encoding() {
        let manifest = sample();
        let limits = test_limits();
        let encoded = manifest.encode(limits).unwrap();
        assert_eq!(manifest.digest(limits).unwrap(), digest(&encoded));
        let hex: String = manifest
            .digest(limits)
            .unwrap()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        assert_eq!(
            hex,
            "8e0964eb9063b95f79c822cbd892a7ea00729bab49e4be0a09fb97167f618615"
        );
        assert!(manifest
            .digest(Limits {
                max_file_size: 2,
                ..limits
            })
            .is_err());
    }
}
