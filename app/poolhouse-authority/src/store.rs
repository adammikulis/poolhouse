use std::fs::{File, Metadata};
use std::io::{self, Read, Write};
use std::os::unix::fs::MetadataExt;
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use super::Hash;

#[allow(unsafe_code)]
mod acl;
#[allow(unsafe_code)]
mod fd;

const MAX_PAYLOAD: usize = 64 * 1024;
const MAX_RECORD: u64 = 512 * 1024;
const STATE: &str = "state.json";
const LOCK: &str = "state.lock";

fn refusal() -> io::Error {
    io::Error::other("Authority storage is unavailable.")
}

fn checked(file: &File, uid: u32, directory: bool, exact: bool) -> io::Result<Metadata> {
    let info = file.metadata()?;
    let mode = info.mode() & 0o7777;
    if info.uid() != uid
        || info.file_type().is_dir() != directory
        || (!directory && (!info.is_file() || info.nlink() != 1))
        || (exact && mode != if directory { 0o700 } else { 0o600 })
        || (!exact && mode & 0o022 != 0)
    {
        return Err(refusal());
    }
    acl::check(file, exact)?;
    Ok(info)
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct Envelope {
    version: u8,
    authority: Hash,
    owner_uid: u32,
    generation: u64,
    head: Hash,
    previous: Hash,
    pristine: bool,
    payload: Vec<u8>,
}

impl Envelope {
    fn digest(&self) -> Hash {
        let mut hash = Sha256::new();
        hash.update(b"poolhouse.native-authority.state.v1\0");
        hash.update(self.authority);
        hash.update(self.owner_uid.to_be_bytes());
        hash.update(self.generation.to_be_bytes());
        hash.update(self.previous);
        hash.update([u8::from(self.pristine)]);
        hash.update((self.payload.len() as u64).to_be_bytes());
        hash.update(&self.payload);
        hash.finalize().into()
    }

    fn validate(&self) -> io::Result<()> {
        if self.version != 1
            || self.authority == [0; 32]
            || self.owner_uid == 0
            || self.payload.len() > MAX_PAYLOAD
            || self.head != self.digest()
            || (self.generation == 0) != self.pristine
            || (self.pristine && (!self.payload.is_empty() || self.previous != [0; 32]))
        {
            return Err(refusal());
        }
        Ok(())
    }
}

struct Guard<'a>(&'a File);
impl Drop for Guard<'_> {
    fn drop(&mut self) {
        let _ = fd::unlock(self.0);
    }
}

struct Store {
    directory: File,
    lock: File,
    owner: u32,
    identity: (Hash, u32),
    poisoned: Mutex<bool>,
    #[cfg(test)]
    fail_after_rename: std::sync::atomic::AtomicBool,
    #[cfg(test)]
    fail_before_rename: std::sync::atomic::AtomicBool,
}

impl Store {
    fn open() -> io::Result<Self> {
        if fd::effective_uid() != 0 {
            return Err(refusal());
        }
        let mut directory = fd::root()?;
        checked(&directory, 0, true, false)?;
        for name in ["Library", "Application Support", "Poolhouse", "Authority"] {
            directory = fd::open_directory(&directory, name)?;
            checked(&directory, 0, true, name == "Authority")?;
        }
        Self::from_directory(directory, 0)
    }

    fn from_directory(directory: File, owner: u32) -> io::Result<Self> {
        checked(&directory, owner, true, true)?;
        let lock = fd::open_file(&directory, LOCK)?;
        checked(&lock, owner, false, true)?;
        fd::lock(&lock)?;
        let _guard = Guard(&lock);
        let initial = Self::read_at(&directory, owner)?;
        drop(_guard);
        Ok(Self {
            directory,
            lock,
            owner,
            identity: (initial.authority, initial.owner_uid),
            poisoned: Mutex::new(false),
            #[cfg(test)]
            fail_after_rename: std::sync::atomic::AtomicBool::new(false),
            #[cfg(test)]
            fail_before_rename: std::sync::atomic::AtomicBool::new(false),
        })
    }

    fn read_at(directory: &File, owner: u32) -> io::Result<Envelope> {
        let file = fd::open_file(directory, STATE)?;
        let before = checked(&file, owner, false, true)?;
        if before.len() > MAX_RECORD {
            return Err(refusal());
        }
        let mut bytes = Vec::new();
        file.take(MAX_RECORD + 1).read_to_end(&mut bytes)?;
        if bytes.len() as u64 > MAX_RECORD {
            return Err(refusal());
        }
        let result: Envelope = serde_json::from_slice(&bytes).map_err(|_| refusal())?;
        result.validate()?;
        Ok(result)
    }

    fn snapshot(&self) -> io::Result<Envelope> {
        let poisoned = self.poisoned.lock().map_err(|_| refusal())?;
        if *poisoned {
            return Err(refusal());
        }
        fd::lock(&self.lock)?;
        let _guard = Guard(&self.lock);
        self.read_bound()
    }

    fn read_bound(&self) -> io::Result<Envelope> {
        checked(&self.directory, self.owner, true, true)?;
        checked(&self.lock, self.owner, false, true)?;
        let value = Self::read_at(&self.directory, self.owner)?;
        if (value.authority, value.owner_uid) != self.identity {
            return Err(refusal());
        }
        Ok(value)
    }

    fn replace(&self, expected: &Envelope, next: &Envelope) -> io::Result<()> {
        let mut poisoned = self.poisoned.lock().map_err(|_| refusal())?;
        if *poisoned {
            return Err(refusal());
        }
        fd::lock(&self.lock)?;
        let _guard = Guard(&self.lock);
        let current = self.read_bound()?;
        next.validate()?;
        if current != *expected
            || (next.authority, next.owner_uid) != self.identity
            || current.generation.checked_add(1) != Some(next.generation)
            || next.pristine
            || next.previous != current.head
        {
            return Err(refusal());
        }
        let result = self.write(next);
        if result.is_err() {
            *poisoned = true;
        }
        result
    }

    fn write(&self, next: &Envelope) -> io::Result<()> {
        let bytes = serde_json::to_vec(next).map_err(|_| refusal())?;
        if bytes.len() as u64 > MAX_RECORD {
            return Err(refusal());
        }
        let mut random = [0u8; 16];
        getrandom::fill(&mut random).map_err(|_| refusal())?;
        let suffix: String = random.iter().map(|byte| format!("{byte:02x}")).collect();
        let name = format!(".state-{suffix}.tmp");
        let mut file = fd::create_file(&self.directory, &name)?;
        let mut renamed = false;
        let result = (|| {
            checked(&file, self.owner, false, true)?;
            #[cfg(test)]
            if self
                .fail_before_rename
                .load(std::sync::atomic::Ordering::SeqCst)
            {
                return Err(refusal());
            }
            file.write_all(&bytes)?;
            file.sync_all()?;
            fd::rename(&self.directory, &name, STATE)?;
            renamed = true;
            #[cfg(test)]
            if self
                .fail_after_rename
                .load(std::sync::atomic::Ordering::SeqCst)
            {
                return Err(refusal());
            }
            self.directory.sync_all()?;
            if self.read_bound()? != *next {
                return Err(refusal());
            }
            Ok(())
        })();
        if let Err(error) = result {
            if !renamed && fd::unlink_owned(&self.directory, &name, &file).is_err() {
                return Err(io::Error::new(
                    error.kind(),
                    format!("{error}; authority temporary cleanup is unconfirmed."),
                ));
            }
            return Err(error);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests;
