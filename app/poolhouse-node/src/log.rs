//! One origin's append-only log on disk: JSON lines, fsynced on append, a torn tail dropped on open. A log starts at seq 1 or at a
//! `base` row, and replaces itself whole when a segment starts.

use std::fs::File;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

use crate::error::{Error, Result};
use crate::fsutil::{private_dir, sync_dir, write_atomic};
use crate::logbounds::Held;
use crate::row::{Kind, Row};
use crate::rules::check_chain;

/// The restated rows a base row says follow it.
pub fn carried(base: &Row) -> u64 {
    base.body.get("carried").and_then(|v| v.as_u64()).unwrap_or(0)
}

/// The rows as JSON lines.
fn lines(rows: &[Row]) -> Result<Vec<u8>> {
    let mut buf = Vec::new();
    for row in rows {
        serde_json::to_writer(&mut buf, row)?;
        buf.push(b'\n');
    }
    Ok(buf)
}

/// The last base row after the first, and how many of its restated rows are still to come.
fn pending_of(rows: &[Row]) -> Option<(usize, u64)> {
    let at = rows.iter().rposition(|r| r.kind == Kind::Base).filter(|at| *at > 0)?;
    let seen = rows[at + 1..].iter().filter(|r| !matches!(r.kind, Kind::Head | Kind::Base)).count() as u64;
    Some((at, carried(&rows[at]).saturating_sub(seen)))
}

pub struct Log {
    path: PathBuf,
    file: File,
    rows: Vec<Row>,
    /// Bytes of the rows held, newlines included.
    bytes: u64,
    /// Bytes of an unfinished last line dropped when the log was opened.
    pub torn_bytes: u64,
    /// A base row after the first one held, at this index, whose restated rows have not all arrived: the rows still to come.
    pending: Option<(usize, u64)>,
}

impl Log {
    /// Open (creating if need be) the log of ``origin`` on ``board``. A last line without its
    /// newline was never acknowledged: it is cut off. Any other damage is an error.
    pub fn open(path: &Path, board: &str, origin: &str) -> Result<Log> {
        let created = !path.exists();
        let mut file = crate::sys::open_log(path)?;
        if created {
            if let Some(parent) = path.parent() {
                sync_dir(parent)?;
            }
        }
        let mut bytes = Vec::new();
        file.read_to_end(&mut bytes)?;
        let good = bytes.iter().rposition(|b| *b == b'\n').map_or(0, |i| i + 1);
        let torn_bytes = (bytes.len() - good) as u64;
        if torn_bytes > 0 {
            file.set_len(good as u64)?;
            file.sync_all()?;
        }
        let mut rows = Vec::new();
        for line in bytes[..good].split(|b| *b == b'\n').filter(|l| !l.is_empty()) {
            let row: Row = serde_json::from_slice(line).map_err(|e| Error::Damaged(format!("a stored row is unreadable: {e}")))?;
            rows.push(row);
        }
        check_chain(&rows, board, origin, None)?;
        let pending = pending_of(&rows);
        Ok(Log { path: path.to_path_buf(), file, rows, bytes: good as u64, torn_bytes, pending })
    }

    /// Every row held, including those of a segment whose restated rows have not all arrived.
    pub fn rows(&self) -> &[Row] {
        &self.rows
    }

    /// The rows a view reads: those before a base whose restated rows are still to come.
    pub fn view(&self) -> &[Row] {
        &self.rows[..self.pending.map_or(self.rows.len(), |(at, _)| at)]
    }

    /// The seq of the first row held, 1 for an empty log.
    pub fn first_seq(&self) -> u64 {
        self.rows.first().map_or(1, |r| r.seq)
    }

    /// The seq of the last row held, 0 for an empty log.
    pub fn last_seq(&self) -> u64 {
        self.rows.last().map_or(0, |r| r.seq)
    }

    /// The row with seq ``seq``, if it is held.
    pub fn at(&self, seq: u64) -> Option<&Row> {
        self.rows.get(usize::try_from(seq.checked_sub(self.first_seq())?).ok()?).filter(|r| r.seq == seq)
    }

    /// How many of the rows held have a seq up to ``seq``.
    pub fn through(&self, seq: u64) -> usize {
        usize::try_from(seq.saturating_add(1).saturating_sub(self.first_seq())).map_or(self.rows.len(), |n| n.min(self.rows.len()))
    }

    /// The rows and bytes from the base a new segment is waiting on, or all of them.
    pub fn segment_held(&self) -> Held {
        let Some((from, _)) = self.pending else { return Held { rows: self.rows.len(), bytes: self.bytes } };
        Held { rows: self.rows.len() - from, bytes: self.rows[from..].iter().map(|r| r.size() as u64 + 1).sum() }
    }

    /// The index of a base whose restated rows have all arrived and whose rows before it are still held.
    pub fn complete(&self) -> Option<usize> {
        self.pending.filter(|(_, left)| *left == 0).map(|(at, _)| at)
    }

    /// The bytes of the rows held, newlines included.
    pub fn bytes(&self) -> u64 {
        self.bytes
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Add one row; it is on disk when this returns.
    pub fn append(&mut self, row: Row) -> Result<()> {
        self.extend(vec![row])
    }

    /// Add rows in one write and one fsync. On a failed write the file is cut back.
    pub fn extend(&mut self, rows: Vec<Row>) -> Result<()> {
        let buf = lines(&rows)?;
        let before = self.file.metadata()?.len();
        let written = self.file.seek(SeekFrom::End(0)).and_then(|_| self.file.write_all(&buf)).and_then(|_| self.file.sync_data());
        if let Err(e) = written {
            let _ = self.file.set_len(before).and_then(|_| self.file.seek(SeekFrom::End(0)));
            return Err(e.into());
        }
        for row in &rows {
            if row.kind == Kind::Base && !self.rows.is_empty() {
                self.pending = Some((self.rows.len(), carried(row)));
            } else if let (false, Some((_, left))) = (matches!(row.kind, Kind::Head | Kind::Base), self.pending.as_mut()) {
                *left = left.saturating_sub(1);
            }
            self.rows.push(row.clone());
        }
        self.bytes += buf.len() as u64;
        Ok(())
    }

    /// Keep the first ``keep`` rows and cut the file after them.
    pub fn truncate(&mut self, keep: usize) -> Result<()> {
        if keep >= self.rows.len() {
            return Ok(());
        }
        let bytes = std::fs::read(&self.path)?;
        let mut ends = bytes.iter().enumerate().filter(|(_, b)| **b == b'\n').map(|(i, _)| i + 1);
        let end = if keep == 0 { 0 } else { ends.nth(keep - 1).unwrap_or(bytes.len()) };
        self.file.set_len(end as u64)?;
        self.file.sync_all()?;
        self.file.seek(SeekFrom::End(0))?;
        self.bytes = end as u64;
        self.rows.truncate(keep);
        self.pending = pending_of(&self.rows);
        Ok(())
    }

    /// Start a new segment: the first ``keep_from`` rows go to a file in ``archive``, and the file of the log is replaced, in one rename,
    /// by the rows from ``keep_from`` on followed by ``extra``.
    pub fn replace(&mut self, keep_from: usize, extra: Vec<Row>, archive: &Path) -> Result<()> {
        let keep_from = keep_from.min(self.rows.len());
        if keep_from > 0 {
            let old = &self.rows[..keep_from];
            private_dir(archive)?;
            write_atomic(&archive.join(format!("{}.{}-{}.jsonl", old[0].origin, old[0].seq, old[keep_from - 1].seq)), &lines(old)?)?;
        }
        let mut rows = self.rows[keep_from..].to_vec();
        rows.extend(extra);
        let buf = lines(&rows)?;
        write_atomic(&self.path, &buf)?;
        self.file = crate::sys::open_log(&self.path)?;
        self.bytes = buf.len() as u64;
        self.pending = pending_of(&rows);
        self.rows = rows;
        Ok(())
    }

    /// Delete the file; the log is gone.
    pub fn remove(self) -> Result<()> {
        std::fs::remove_file(&self.path)?;
        Ok(())
    }
}
