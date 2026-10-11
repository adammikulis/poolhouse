//! `poolhouse-node run --state DIR` serves the node; `status` talks to a running one.
//!
//! The network is off unless `--listen ADDR` names where to listen, or `--lan` says to listen on
//! every interface and beacon on the multicast group (ports 7447 and 7448). `--beacon-bind ADDR` turns
//! the beacon on, `--beacon-send ADDR` (repeatable) says where to send it (the multicast
//! group in production, a peer in tests), `--advertise IP` the address it carries,
//! `--allow-loopback` lets a test advertise loopback, `--sync-ms N` sets the sync interval.
//!
//! `--presence auto|terminal` says how the first person key and the first phone are asked of the person (default `auto`: the operating
//! system's own prompt; `terminal`: the terminal device the asker names, which the node itself reads).
//!
//! With the beacon on, the device finds a pool without a key or a code: it takes the project of
//! the repository it runs in (`--project-dir DIR`, else the working directory; `--project NAME`
//! names one instead; `--no-project` turns this off), listens `--settle-ms N` for an `open` pool
//! of that project to join, and makes one when it hears none.

use std::io::{BufRead, Read};
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Duration;

use poolhouse_node::beacon::{BeaconConfig, GROUP};
use poolhouse_node::client::Client;
use poolhouse_node::device::{Custody, Supplied};
use poolhouse_node::fsutil::unhex;
use poolhouse_node::net::{Net, NetConfig, SETTLE};
use poolhouse_node::projectid;
use poolhouse_node::server::Server;
use serde_json::json;

/// What `--lan` stands for: listen on every interface and beacon to the multicast group.
const LAN_LISTEN: &str = "0.0.0.0:7447";
const LAN_BEACON_PORT: u16 = 7448;

fn state_dir(args: &[String]) -> PathBuf {
    match flag(args, "--state") {
        Some(dir) => PathBuf::from(dir),
        None => poolhouse_node::sys::home_dir().join(".poolhouse").join("node"),
    }
}

fn flag<'a>(args: &'a [String], name: &str) -> Option<&'a str> {
    args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).map(String::as_str)
}

fn addr(text: &str) -> Result<SocketAddr, String> {
    text.parse::<SocketAddr>().map_err(|e| format!("{text}: {e}"))
}

fn beacon_config(args: &[String], bind: &str) -> Result<BeaconConfig, String> {
    let mut send_to = args.windows(2).filter(|w| w[0] == "--beacon-send").map(|w| addr(&w[1])).collect::<Result<Vec<_>, _>>()?;
    if send_to.is_empty() && lan(args) {
        send_to.push(SocketAddr::from((GROUP, LAN_BEACON_PORT)));
    }
    let advertise = flag(args, "--advertise").map(|a| a.parse::<IpAddr>().map_err(|e| e.to_string())).transpose()?;
    let interface = flag(args, "--multicast-if").map(|a| a.parse::<Ipv4Addr>().map_err(|e| format!("--multicast-if: {e}"))).transpose()?;
    Ok(BeaconConfig {
        bind: addr(bind)?, send_to, advertise, interval: Duration::from_millis(500), allow_loopback: args.iter().any(|a| a == "--allow-loopback"),
        interface, broadcast: lan(args),
    })
}

/// The project key: the named project, else that of the repository around the directory; None
/// outside a repository when none is named.
fn project_key(args: &[String]) -> Result<Option<String>, String> {
    let over = flag(args, "--project");
    let dir = flag(args, "--project-dir").map(PathBuf::from).or_else(|| std::env::current_dir().ok()).unwrap_or_default();
    match projectid::project_of(&dir, over) {
        Ok(key) => Ok(Some(key)),
        Err(_) if over.is_none() => Ok(None),
        Err(e) => Err(e.to_string()),
    }
}

fn lan(args: &[String]) -> bool {
    args.iter().any(|a| a == "--lan")
}

fn net_config(args: &[String]) -> Result<Option<NetConfig>, String> {
    let Some(listen) = flag(args, "--listen").or(lan(args).then_some(LAN_LISTEN)) else { return Ok(None) };
    let beacon_bind = format!("0.0.0.0:{LAN_BEACON_PORT}");
    let beacon = flag(args, "--beacon-bind").or(lan(args).then_some(beacon_bind.as_str())).map(|b| beacon_config(args, b)).transpose()?;
    let sync_every = flag(args, "--sync-ms").map(|n| n.parse::<u64>().map(Duration::from_millis).map_err(|e| e.to_string())).transpose()?;
    let settle = flag(args, "--settle-ms").map(|n| n.parse::<u64>().map(Duration::from_millis).map_err(|e| e.to_string())).transpose()?;
    let project = if beacon.is_some() && !args.iter().any(|a| a == "--no-project") { project_key(args)? } else { None };
    Ok(Some(NetConfig { listen: addr(listen)?, beacon, sync_every, project, settle: settle.unwrap_or(SETTLE) }))
}

/// The build this node advertises: `--build-id`, `--build-seq`, `--wire-version` and `--pinned` over the environment.
fn set_build(node: &mut poolhouse_node::node::Node, args: &[String]) -> Result<(), String> {
    let number = |name: &str| flag(args, name).map(|n| n.parse::<u64>().map_err(|e| format!("{name}: {e}"))).transpose();
    if let Some(id) = flag(args, "--build-id") {
        node.build.id = id.chars().take(64).collect();
    }
    if let Some(seq) = number("--build-seq")? {
        node.build.seq = seq;
    }
    if let Some(wire) = number("--wire-version")? {
        node.build.wire = u32::try_from(wire).map_err(|e| e.to_string())?;
    }
    node.build.pinned |= args.iter().any(|a| a == "--pinned");
    Ok(())
}

/// Where the node listens for phones: `--phone ADDR`, else the address `phone.json` in the state directory holds (a person's command
/// writes it); None when neither names one.
fn phone_listen(state: &Path, args: &[String]) -> Result<Option<SocketAddr>, String> {
    if let Some(a) = flag(args, "--phone") {
        return addr(a).map(Some);
    }
    match std::fs::read(state.join("phone.json")) {
        Ok(bytes) => {
            let listen = serde_json::from_slice::<serde_json::Value>(&bytes).ok().and_then(|v| v["listen"].as_str().map(str::to_string));
            listen.ok_or_else(|| "phone.json names no listen address".to_string()).and_then(|a| addr(&a)).map(Some)
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(format!("phone.json: {e}")),
    }
}

/// The key the node was told to run as: one line of 64 hex digits on standard input when ``--key-stdin`` is given, with where it is
/// kept (``--key-custody keystore|passphrase``, default keystore). Whatever starts the node gives it; nothing the node writes holds it.
fn supplied_key(args: &[String]) -> Result<Option<Supplied>, String> {
    if !args.iter().any(|a| a == "--key-stdin") {
        return Ok(None);
    }
    let custody = flag(args, "--key-custody").map_or(Some(Custody::Keystore), Custody::parse).filter(|c| *c != Custody::File)
        .ok_or("--key-custody is keystore or passphrase")?;
    let mut line = String::new();
    std::io::stdin().lock().take(130).read_line(&mut line).map_err(|e| format!("the key on standard input: {e}"))?;
    let seed: [u8; 32] = unhex(line.trim()).and_then(|b| b.try_into().ok()).ok_or("standard input did not hold a key: 64 hex digits and a newline")?;
    no_core_dumps();
    Ok(Some(Supplied { seed, custody }))
}

/// A node that holds a key it was given leaves no core file behind, which would hold the key.
fn no_core_dumps() {
    #[cfg(unix)]
    {
        let none = libc::rlimit { rlim_cur: 0, rlim_max: 0 };
        // SAFETY: setrlimit reads the one plain-data struct it is given.
        unsafe { libc::setrlimit(libc::RLIMIT_CORE, &none) };
    }
}

fn run(state: &Path, args: &[String]) -> Result<(), String> {
    let config = net_config(args)?;
    let phone = phone_listen(state, args)?;
    let mut server = Server::bind_with(state, supplied_key(args)?.as_ref()).map_err(|e| e.to_string())?;
    let shared = server.node();
    set_build(&mut *shared.lock().map_err(|e| e.to_string())?, args)?;
    if let Some(name) = flag(args, "--presence") {
        let mode = poolhouse_node::presence::Mode::parse(name).ok_or("--presence is auto (the operating system's prompt) or terminal (the person's terminal device)")?;
        shared.lock().map_err(|e| e.to_string())?.firsts = poolhouse_node::presence::Firsts::new(mode.presence());
    }
    if let Some(listen) = phone {
        shared.lock().map_err(|e| e.to_string())?.pusher = Box::new(poolhouse_node::phone::push::Web::start(state));
        poolhouse_node::phone::http::start(shared.clone(), listen).map_err(|e| e.to_string())?;
    }
    if let Some(config) = config {
        let net = Net::start(server.node(), config).map_err(|e| e.to_string())?;
        server = server.attach(net);
    }
    server.serve().map_err(|e| e.to_string())
}

/// `probe`: the local-network check (`poolhouse_node::probe`); its JSON line goes to `--out FILE` when given (a bundled app
/// has no terminal), else to stdout.
fn probe(args: &[String]) -> Result<String, String> {
    let seconds = flag(args, "--seconds").map(|n| n.parse::<u64>().map_err(|e| format!("--seconds: {e}"))).transpose()?.unwrap_or(10);
    let peers = args.windows(2).filter(|w| w[0] == "--peer").map(|w| w[1].parse::<Ipv4Addr>().map_err(|e| format!("--peer {}: {e}", w[1]))).collect::<Result<Vec<_>, _>>()?;
    let found = poolhouse_node::probe::run(seconds, &peers).map_err(|e| e.to_string());
    match (flag(args, "--out"), found) {
        (Some(file), found) => {
            let text = found.unwrap_or_else(|e| json!({"error": e})).to_string();
            std::fs::write(file, text).map(|_| String::new()).map_err(|e| e.to_string())
        }
        (None, found) => found.map(|v| v.to_string()),
    }
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let state = state_dir(&args);
    let result = match args.first().map(String::as_str) {
        Some("run") => run(&state, &args).map(|_| String::new()),
        Some("status") => Client::connect(&state).and_then(|mut c| c.call("status", "", "", json!({}))).map(|v| v.to_string()).map_err(|e| e.to_string()),
        Some("probe") => probe(&args),
        _ => {
            eprintln!("usage: poolhouse-node run|status|probe [--state DIR] [--listen ADDR ...] [--seconds N] [--out FILE]");
            return ExitCode::from(2);
        }
    };
    match result {
        Ok(text) => {
            if !text.is_empty() {
                println!("{text}");
            }
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("poolhouse-node: {e}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(all(test, unix))]
mod tests {
    #[test]
    fn a_node_that_holds_a_given_key_cannot_write_a_core_file() {
        super::no_core_dumps();
        let mut now = libc::rlimit { rlim_cur: 1, rlim_max: 1 };
        // SAFETY: getrlimit writes the one plain-data struct it is given.
        assert_eq!(unsafe { libc::getrlimit(libc::RLIMIT_CORE, &mut now) }, 0);
        assert_eq!((now.rlim_cur, now.rlim_max), (0, 0));
    }
}
