//! The project registry: which board a directory belongs to.
//!
//! A project is a board id and the places its work lives: a git repository, a plain folder, a
//! cloud-synced folder (its local sync path). A client's working directory
//! resolves to the project whose source contains it; a linked git worktree resolves through
//! the repository's common directory. A directory that matches nothing is not part of any
//! project, and is never given another project's board. A place is never inside or around a
//! place of another project, so no project takes over where another's sessions land. A place is
//! never the user's home, a directory around it or the root of the disk, and a folder that is not
//! a git repository never holds a repository of its own.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::error::{Error, Result};
use crate::fsutil::write_atomic;
use crate::project::common_root;
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

/// The main working tree of the repository containing ``dir``, if it is in one (`project::common_root`).
pub fn git_root(dir: &Path) -> Option<PathBuf> {
    common_root(dir.ancestors().find(|d| d.join(".git").exists())?).ok()
}

/// A place is never the user's home, a directory around it, or the root of the disk: it would hold every directory the user works in.
fn refuse_broad(path: &Path) -> Result<()> {
    let home = crate::sys::home_dir().canonicalize().ok();
    if path.parent().is_none() || home.is_some_and(|h| h.starts_with(path)) {
        return Err(Error::Invalid("a project's place is a directory below the user's home, never the home itself, a directory around it or the root of the disk".into()));
    }
    Ok(())
}

/// How deep and how many entries a folder is searched for a git repository of its own.
const SCAN_DEPTH: usize = 3;
const SCAN_ENTRIES: usize = 4000;

/// A git repository below ``place`` (not ``place`` itself), found within a bounded search that does not follow links.
fn nested_repository(place: &Path) -> Option<PathBuf> {
    let (mut level, mut seen) = (vec![place.to_path_buf()], 0);
    for _ in 0..=SCAN_DEPTH {
        let mut next = Vec::new();
        for dir in &level {
            for entry in std::fs::read_dir(dir).into_iter().flatten().flatten() {
                seen += 1;
                let name = entry.file_name();
                if seen > SCAN_ENTRIES || name == ".git" || name == "node_modules" || !entry.file_type().is_ok_and(|t| t.is_dir()) {
                    continue;
                }
                if entry.path().join(".git").exists() {
                    return Some(entry.path());
                }
                next.push(entry.path());
            }
        }
        level = next;
    }
    None
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
        refuse_broad(&path)?;
        if kind != "git" {
            if let Some(repo) = nested_repository(&path) {
                return Err(Error::Invalid(format!("that folder holds the git repository {}: add the repository as a project of its own", repo.display())));
            }
        }
        Ok(Source { kind: kind.into(), path })
    }

    /// Register a new project with its first source.
    pub fn add(&mut self, id: &str, kind: &str, path: &str) -> Result<Project> {
        if !valid_name(id) || self.get(id).is_some() {
            return Err(Error::Invalid("a project id is a new short lower-case word".into()));
        }
        if self.all.len() >= MAX_PROJECTS {
            return Err(crate::limits::refuse("a node holds as many projects as it may", MAX_PROJECTS, "MAX_PROJECTS", "registry.rs"));
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
            return Err(crate::limits::refuse("a project keeps as many sources as it may", MAX_SOURCES, "MAX_SOURCES", "registry.rs"));
        }
        if !project.sources.contains(&source) {
            project.sources.push(source);
        }
        let done = project.clone();
        self.save()?;
        Ok(done)
    }

    /// Forget the project ``id``: its places are free for another project to take; the project's board is left as it is.
    pub fn remove(&mut self, id: &str) -> Result<Project> {
        let at = self.all.iter().position(|p| p.id == id).ok_or_else(|| Error::Invalid("no such project".into()))?;
        let mut rest = self.all.clone();
        let gone = rest.remove(at);
        write_atomic(&self.path, &serde_json::to_vec(&rest)?)?;
        self.all = rest;
        Ok(gone)
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

    #[test]
    fn a_removed_project_frees_its_place_for_another_and_stays_removed_when_reopened() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("projects.json");
        let place = dir.path().join("work");
        std::fs::create_dir(&place).unwrap();
        let mut projects = Projects::open(&file).unwrap();
        projects.add("first", "folder", place.to_str().unwrap()).unwrap();
        assert!(projects.add("second", "folder", place.to_str().unwrap()).is_err(), "a place belongs to one project");
        assert_eq!(projects.remove("first").unwrap().id, "first");
        assert!(projects.get("first").is_none() && projects.resolve(place.to_str().unwrap()).is_err());
        assert!(matches!(projects.remove("first"), Err(Error::Invalid(_))), "an id that is gone is not a project");
        projects.add("second", "folder", place.to_str().unwrap()).unwrap();
        assert_eq!(Projects::open(&file).unwrap().all().iter().map(|p| p.id.as_str()).collect::<Vec<_>>(), ["second"]);
    }
}
