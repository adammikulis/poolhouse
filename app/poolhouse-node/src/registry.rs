//! The project registry: which board a directory belongs to.
//!
//! A project is a board id and the places its work lives: a git repository, a plain folder, a
//! cloud-synced folder (its local sync path). A client's working directory
//! resolves to the project whose source contains it; a linked git worktree resolves through
//! the repository's common directory. A directory that matches nothing is not part of any
//! project, and is never given another project's board. A place is never inside or around a
//! place of another project, so no project takes over where another's sessions land.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::error::{Error, Result};
use crate::fsutil::write_atomic;
use crate::row::valid_name;

pub const KINDS: [&str; 4] = ["git", "folder", "gdrive", "onedrive"];
const MAX_SOURCES: usize = 32;
/// How many projects a node holds.
pub const MAX_PROJECTS: usize = 256;

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct Source {
    pub kind: String,
    pub path: PathBuf,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct Project {
    pub id: String,
    pub sources: Vec<Source>,
}

pub struct Projects {
    path: PathBuf,
    all: Vec<Project>,
}

/// The main working tree of the repository containing ``dir``, if it is in one.
pub fn git_root(dir: &Path) -> Option<PathBuf> {
    let top = dir.ancestors().find(|d| d.join(".git").exists())?;
    let git = top.join(".git");
    if git.is_dir() {
        return Some(top.to_path_buf());
    }
    let text = std::fs::read_to_string(&git).ok()?;
    let gitdir = PathBuf::from(text.trim().strip_prefix("gitdir:")?.trim());
    let gitdir = if gitdir.is_absolute() { gitdir } else { top.join(gitdir) };
    let common = match std::fs::read_to_string(gitdir.join("commondir")) {
        Ok(rel) => gitdir.join(rel.trim()).canonicalize().ok()?,
        Err(_) => gitdir.canonicalize().ok()?,
    };
    common.parent().map(Path::to_path_buf)
}

impl Projects {
    pub fn open(path: &Path) -> Result<Projects> {
        let all = match std::fs::read(path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| Error::Damaged(format!("projects unreadable: {e}")))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Vec::new(),
            Err(e) => return Err(e.into()),
        };
        Ok(Projects { path: path.into(), all })
    }

    pub fn all(&self) -> &[Project] {
        &self.all
    }

    pub fn get(&self, id: &str) -> Option<&Project> {
        self.all.iter().find(|p| p.id == id)
    }

    fn save(&self) -> Result<()> {
        write_atomic(&self.path, &serde_json::to_vec(&self.all)?)
    }

    fn source(kind: &str, path: &str) -> Result<Source> {
        if !KINDS.contains(&kind) {
            return Err(Error::Invalid("a source is git, folder, gdrive or onedrive".into()));
        }
        let real = Path::new(path).canonicalize().map_err(|_| Error::Invalid("the source path does not exist".into()))?;
        let path = if kind == "git" { git_root(&real).ok_or_else(|| Error::Invalid("the path is not in a git repository".into()))? } else { real };
        Ok(Source { kind: kind.into(), path })
    }

    /// Register a new project with its first source.
    pub fn add(&mut self, id: &str, kind: &str, path: &str) -> Result<Project> {
        if !valid_name(id) || self.get(id).is_some() {
            return Err(Error::Invalid("a project id is a new short lower-case word".into()));
        }
        if self.all.len() >= MAX_PROJECTS {
            return Err(Error::Quota(format!("a node holds at most {MAX_PROJECTS} projects")));
        }
        let source = Projects::source(kind, path)?;
        self.claim_check(id, &source)?;
        self.all.push(Project { id: id.into(), sources: vec![source] });
        self.save()?;
        Ok(self.all.last().cloned().expect("pushed above"))
    }

    /// Add a source to an existing project.
    pub fn add_source(&mut self, id: &str, kind: &str, path: &str) -> Result<Project> {
        let source = Projects::source(kind, path)?;
        self.claim_check(id, &source)?;
        let project = self.all.iter_mut().find(|p| p.id == id).ok_or_else(|| Error::Invalid("no such project".into()))?;
        if project.sources.len() >= MAX_SOURCES {
            return Err(Error::Quota("a project keeps this many sources at most".into()));
        }
        if !project.sources.contains(&source) {
            project.sources.push(source);
        }
        let done = project.clone();
        self.save()?;
        Ok(done)
    }

    /// A place may not be, lie inside, or hold a place of another project.
    fn claim_check(&self, id: &str, source: &Source) -> Result<()> {
        let overlaps = |s: &Source| source.path.starts_with(&s.path) || s.path.starts_with(&source.path);
        match self.all.iter().find(|p| p.id != id && p.sources.iter().any(overlaps)) {
            Some(other) => Err(Error::Invalid(format!("that place is, is inside or holds a place of project {}", other.id))),
            None => Ok(()),
        }
    }

    /// The project whose source contains ``dir``, or `Denied` saying the directory is not part of a project.
    pub fn resolve(&self, dir: &str) -> Result<&Project> {
        let not_part = || Error::Denied("this directory is not part of a project: register it with project_add".into());
        let real = Path::new(dir).canonicalize().map_err(|_| not_part())?;
        let mut places = vec![real.clone()];
        places.extend(git_root(&real));
        self.all.iter().find(|p| p.sources.iter().any(|s| places.iter().any(|d| d.starts_with(&s.path)))).ok_or_else(not_part)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_node_holds_a_bounded_number_of_projects() {
        let dir = tempfile::tempdir().unwrap();
        let mut projects = Projects::open(&dir.path().join("projects.json")).unwrap();
        for i in 0..MAX_PROJECTS {
            let place = dir.path().join(format!("p{i}"));
            std::fs::create_dir(&place).unwrap();
            projects.add(&format!("p{i}"), "folder", place.to_str().unwrap()).unwrap();
        }
        let more = dir.path().join("more");
        std::fs::create_dir(&more).unwrap();
        assert!(matches!(projects.add("more", "folder", more.to_str().unwrap()), Err(Error::Quota(_))));
    }
}
