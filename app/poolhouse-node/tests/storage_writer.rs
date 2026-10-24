use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use poolhouse_node::error::Error;
use poolhouse_node::node::Node;
use poolhouse_node::server::Server;
mod kit;
use kit::short_dir;

fn bytes(root: &Path) -> BTreeMap<PathBuf, Vec<u8>> {
    fn visit(root: &Path, at: &Path, out: &mut BTreeMap<PathBuf, Vec<u8>>) {
        for entry in std::fs::read_dir(at).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() { visit(root, &path, out); }
            else if path.is_file() { out.insert(path.strip_prefix(root).unwrap().into(), std::fs::read(path).unwrap()); }
        }
    }
    let mut out=BTreeMap::new(); visit(root,root,&mut out); out
}

#[test]
fn retained_node_keeps_writer_lock_after_listener_drops() {
    let dir=short_dir();
    let server=Server::bind(dir.path()).unwrap();
    let retained=server.node();
    let last=retained.clone();
    drop(server);
    // The surviving actor can still write actual node storage after listener ownership ended.
    retained.lock().unwrap().host("retained").unwrap();
    let before=bytes(dir.path());
    assert!(matches!(Node::open(dir.path()),Err(Error::Denied(_))));
    assert!(matches!(Server::bind(dir.path()),Err(Error::Denied(_))));
    assert_eq!(bytes(dir.path()),before,"refused opens must not touch stored data");
    drop(retained);
    assert!(matches!(Node::open(dir.path()),Err(Error::Denied(_))));
    drop(last);
    assert!(Node::open(dir.path()).is_ok(),"only the final writable owner releases custody");
}

#[test]
fn direct_writable_nodes_also_exclude_other_writers() {
    let dir=short_dir();
    let node=Node::open(dir.path()).unwrap();
    let before=bytes(dir.path());
    assert!(matches!(Node::open(dir.path()),Err(Error::Denied(_))));
    assert!(matches!(Server::bind(dir.path()),Err(Error::Denied(_))));
    assert_eq!(bytes(dir.path()),before);
    drop(node);
    assert!(Server::bind(dir.path()).is_ok());
}
