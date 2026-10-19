mod kit;

use kit::*;
use serde_json::json;
use std::path::Path;
use std::process::Command;
use tempfile::tempdir;

fn git(dir: &Path, args: &[&str]) {
    let out = Command::new("git").arg("-C").arg(dir).args(["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]).args(args).output().unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
}

fn resolve(n: &mut poolhouse_node::node::Node, path: &Path) -> serde_json::Value {
    req(n, "project_resolve", "", "", json!({"path": path.to_str().unwrap()}))
}

/// ``id`` added as a project by a session of its own board, which holds `source_add` when the place is not a git repository.
fn add(n: &mut poolhouse_node::node::Node, id: &str, kind: &str, path: &Path) -> serde_json::Value {
    let (name, token) = session(n, id, "placer");
    if kind != "git" {
        allow(n, id, &token, &name, &["source_add"]);
    }
    add_as(n, &token, id, kind, path)
}

fn add_as(n: &mut poolhouse_node::node::Node, token: &str, id: &str, kind: &str, path: &Path) -> serde_json::Value {
    req(n, "project_add", "", token, json!({"id": id, "kind": kind, "path": path.to_str().unwrap()}))
}

#[test]
fn a_folder_only_project_resolves_from_inside_it() {
    let (state, work) = (tempdir().unwrap(), tempdir().unwrap());
    let docs = work.path().join("drive/Specs");
    std::fs::create_dir_all(docs.join("2026/q4")).unwrap();
    let mut n = node(state.path());
    ok(add(&mut n, "specs", "gdrive", &docs));
    assert_eq!(ok(resolve(&mut n, &docs.join("2026/q4")))["board"], "specs");
    assert_eq!(ok(resolve(&mut n, &docs))["board"], "specs");
    drop(n);
    let mut n = node(state.path());
    assert_eq!(ok(resolve(&mut n, &docs.join("2026")))["board"], "specs", "the registry survives a restart");
    assert_eq!(ok(req(&mut n, "project_list", "", "", json!({})))[0]["sources"][0]["kind"], "gdrive");
}

#[test]
fn a_repository_and_all_its_worktrees_resolve_to_its_project() {
    let (state, work) = (tempdir().unwrap(), tempdir().unwrap());
    let main = work.path().join("shop");
    std::fs::create_dir(&main).unwrap();
    git(&main, &["init", "-q"]);
    git(&main, &["commit", "-q", "--allow-empty", "-m", "start"]);
    let linked = work.path().join("shop-feature");
    git(&main, &["worktree", "add", "-q", linked.to_str().unwrap(), "-b", "feature"]);
    std::fs::create_dir_all(linked.join("src")).unwrap();
    let mut n = node(state.path());
    ok(add(&mut n, "shop", "git", &linked.join("src")));
    for dir in [main.clone(), linked.clone(), linked.join("src")] {
        assert_eq!(ok(resolve(&mut n, &dir))["board"], "shop", "{dir:?}");
    }
    let sources = ok(req(&mut n, "project_list", "", "", json!({})));
    assert_eq!(Path::new(sources[0]["sources"][0]["path"].as_str().unwrap()), main.canonicalize().unwrap(), "a git source is the repository, not the worktree");
}

#[test]
fn an_unregistered_directory_is_not_part_of_a_project_and_never_gets_another_board() {
    let (state, work) = (tempdir().unwrap(), tempdir().unwrap());
    let (mine, stranger, sibling) = (work.path().join("mine"), work.path().join("stranger"), work.path().join("mine-sibling"));
    for d in [&mine, &stranger, &sibling] {
        std::fs::create_dir(d).unwrap();
    }
    let mut n = node(state.path());
    ok(add(&mut n, "mine", "folder", &mine));
    for dir in [&stranger, &sibling, work.path(), Path::new("/definitely/not/here")] {
        let reply = resolve(&mut n, dir);
        assert_eq!(code(&reply), "denied", "{dir:?}");
        assert!(reply["error"]["message"].as_str().unwrap().contains("not part of a project"));
    }
}

#[test]
fn a_place_is_never_inside_around_or_the_same_as_a_place_of_another_project() {
    let (state, work) = (tempdir().unwrap(), tempdir().unwrap());
    let (mono, inner, around) = (work.path().join("mono"), work.path().join("mono/service"), work.path().join("elsewhere/deep"));
    std::fs::create_dir_all(&inner).unwrap();
    std::fs::create_dir_all(&around).unwrap();
    let mut n = node(state.path());
    ok(add(&mut n, "mono", "folder", &mono));
    assert_eq!(code(&add(&mut n, "service", "folder", &inner)), "invalid", "a place inside another project's place is refused");
    assert_eq!(code(&add(&mut n, "thief", "folder", &mono)), "invalid", "and so is the very same place");
    assert_eq!(code(&add(&mut n, "wide", "folder", work.path())), "invalid", "and one that holds another project's place");
    ok(add(&mut n, "deep", "folder", &around));
    assert_eq!(code(&add(&mut n, "shallow", "folder", around.parent().unwrap())), "invalid", "and the place above one");
    assert_eq!(ok(resolve(&mut n, &inner))["board"], "mono", "a directory inside a project is that project's");
    let (second, token) = session(&mut n, "mono", "second");
    allow(&mut n, "mono", &token, &second, &["source_add"]);
    assert_eq!(code(&add_as(&mut n, &token, "mono", "folder", &inner)), "invalid", "an id is registered once");
    assert_eq!(code(&add_as(&mut n, &token, "BAD", "folder", &around)), "denied", "a session adds the project of its own board only");
    assert_eq!(code(&add(&mut n, "k", "dropbox", &inner)), "invalid");
}

#[test]
fn a_project_is_added_by_a_session_of_its_own_board() {
    let (state, work) = (tempdir().unwrap(), tempdir().unwrap());
    let mut n = node(state.path());
    let place = |name: &str| {
        let dir = work.path().join(name);
        std::fs::create_dir(&dir).unwrap();
        dir
    };
    let (mine, other) = (place("mine"), place("other"));
    assert_eq!(code(&add_as(&mut n, "", "mine", "folder", &mine)), "denied", "no session, no project");
    assert_eq!(code(&add_as(&mut n, "not-a-token", "mine", "folder", &mine)), "denied");
    let (name, token) = session(&mut n, "mine", "s");
    allow(&mut n, "mine", &token, &name, &["source_add"]);
    assert_eq!(code(&add_as(&mut n, &token, "other", "folder", &other)), "denied", "a session adds the project of its own board only");
    ok(add_as(&mut n, &token, "mine", "folder", &mine));
    for i in 0..9 {
        ok(add(&mut n, &format!("extra-{i}"), "folder", &place(&format!("extra-{i}"))));
    }
    ok(add(&mut n, "eleventh", "folder", &place("eleventh")));
    assert!(ok(req(&mut n, "project_list", "", "", json!({}))).as_array().unwrap().len() == 11);
}

#[test]
fn only_a_session_of_the_project_adds_a_place_to_it() {
    let (state, work) = (tempdir().unwrap(), tempdir().unwrap());
    let (a, b, extra) = (work.path().join("a"), work.path().join("b"), work.path().join("extra"));
    for d in [&a, &b, &extra] {
        std::fs::create_dir(d).unwrap();
    }
    let mut n = node(state.path());
    ok(add(&mut n, "alpha", "folder", &a));
    ok(add(&mut n, "beta", "folder", &b));
    let (alpha_name, alpha) = session(&mut n, "alpha", "s");
    let (beta_name, beta) = session(&mut n, "beta", "s");
    let source = |n: &mut poolhouse_node::node::Node, token: &str| req(n, "source_add", "", token, json!({"id": "alpha", "kind": "onedrive", "path": extra.to_str().unwrap()}));
    assert_eq!(code(&source(&mut n, "")), "denied");
    assert_eq!(code(&source(&mut n, &beta)), "denied");
    let held = source(&mut n, &alpha);
    assert_eq!((code(&held), held["error"]["grant"]["action"].clone(), held["error"]["grant"]["resource"].clone()), ("denied".to_string(), json!("source_add"), json!("alpha")), "a place is added by a grant");
    allow(&mut n, "beta", &beta, &beta_name, &["source_add"]);
    assert_eq!(code(&source(&mut n, &beta)), "denied", "a grant on beta is not a grant on alpha");
    allow(&mut n, "alpha", &alpha, &alpha_name, &["source_add"]);
    ok(source(&mut n, &alpha));
    assert_eq!(ok(resolve(&mut n, &extra))["board"], "alpha");
}

#[test]
fn a_place_around_the_users_home_or_the_whole_disk_is_no_project() {
    let state = tempdir().unwrap();
    let mut n = node(state.path());
    let home = poolhouse_node::sys::home_dir().canonicalize().unwrap();
    let (name, token) = session(&mut n, "evil", "placer");
    allow(&mut n, "evil", &token, &name, &["source_add"]);
    for place in [Path::new("/"), home.as_path(), home.parent().unwrap()] {
        let reply = add_as(&mut n, &token, "evil", "folder", place);
        assert_eq!(code(&reply), "invalid", "{place:?}: {reply}");
        assert!(reply["error"]["message"].as_str().unwrap().contains("home"), "{reply}");
    }
    assert!(ok(req(&mut n, "project_list", "", "", json!({}))).as_array().unwrap().is_empty(), "nothing was registered");
    assert_eq!(code(&resolve(&mut n, &home)), "denied", "so no directory of the user's resolves to the board `evil`");
}

#[test]
fn a_folder_that_holds_a_git_repository_of_its_own_is_not_one_place() {
    let (state, work) = (tempdir().unwrap(), tempdir().unwrap());
    let (broad, repo) = (work.path().join("everything"), work.path().join("everything/shop"));
    std::fs::create_dir_all(&repo).unwrap();
    git(&repo, &["init", "-q"]);
    git(&repo, &["commit", "-q", "--allow-empty", "-m", "start"]);
    let mut n = node(state.path());
    let reply = add(&mut n, "wide", "folder", &broad);
    assert_eq!(code(&reply), "invalid", "{reply}");
    assert!(reply["error"]["message"].as_str().unwrap().contains("git repository"), "{reply}");
    ok(add(&mut n, "shop", "git", &repo));
    assert_eq!(ok(resolve(&mut n, &repo))["board"], "shop", "the repository is its own project");
}

#[test]
fn a_place_that_is_not_a_git_repository_is_added_by_a_session_that_holds_source_add() {
    let (state, work) = (tempdir().unwrap(), tempdir().unwrap());
    let (docs, repo) = (work.path().join("docs"), work.path().join("shop"));
    std::fs::create_dir_all(&docs).unwrap();
    std::fs::create_dir_all(&repo).unwrap();
    git(&repo, &["init", "-q"]);
    git(&repo, &["commit", "-q", "--allow-empty", "-m", "start"]);
    let mut n = node(state.path());
    let (name, token) = session(&mut n, "specs", "placer");
    let held = add_as(&mut n, &token, "specs", "folder", &docs);
    assert_eq!((code(&held), held["error"]["grant"]["action"].clone(), held["error"]["grant"]["resource"].clone()), ("denied".to_string(), json!("source_add"), json!("specs")), "{held}");
    assert_eq!(code(&resolve(&mut n, &docs)), "denied", "a refused project is not registered");
    for kind in ["gdrive", "onedrive"] {
        assert_eq!(code(&add_as(&mut n, &token, "specs", kind, &docs)), "denied", "{kind} is no git repository either");
    }
    let (_, git_token) = session(&mut n, "shop", "placer");
    ok(add_as(&mut n, &git_token, "shop", "git", &repo));

    allow(&mut n, "specs", &token, &name, &["source_add"]);
    ok(add_as(&mut n, &token, "specs", "folder", &docs));
    assert_eq!(ok(resolve(&mut n, &docs))["board"], "specs");
    let log = ok(req(&mut n, "grant_log", "specs", &token, json!({"limit": 20})))["log"].clone();
    assert!(log.as_array().unwrap().iter().any(|e| e["op"] == "use" && e["action"] == "source_add" && e["target"] == "specs"), "the use is on the board: {log}");
}
