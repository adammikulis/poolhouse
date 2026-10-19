//! Every refusal of a limit is built by `limits::refuse`, which names the limit, its value and the setting that holds it, and the
//! setting it names exists.

use std::path::{Path, PathBuf};

fn sources(dir: &Path, out: &mut Vec<PathBuf>) {
    let mut entries: Vec<PathBuf> = std::fs::read_dir(dir).unwrap().map(|e| e.unwrap().path()).collect();
    entries.sort();
    for path in entries {
        if path.is_dir() {
            sources(&path, out);
        } else if path.extension().is_some_and(|e| e == "rs") {
            out.push(path);
        }
    }
}

fn source_files() -> Vec<(String, String)> {
    let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut paths = Vec::new();
    sources(&src, &mut paths);
    paths.into_iter().map(|p| (p.strip_prefix(&src).unwrap().to_string_lossy().replace('\\', "/"), std::fs::read_to_string(&p).unwrap())).collect()
}

/// The last two string literals of ``line``: the key and the file a `refuse` call ends with.
fn last_two_literals(line: &str) -> Option<(String, String)> {
    let mut found: Vec<String> = Vec::new();
    let (mut open, mut escaped, mut text) = (false, false, String::new());
    for c in line.chars() {
        match (open, c) {
            (true, _) if escaped => {
                escaped = false;
                text.push(c);
            }
            (true, '\\') => escaped = true,
            (true, '"') => {
                open = false;
                found.push(std::mem::take(&mut text));
            }
            (true, _) => text.push(c),
            (false, '"') => open = true,
            (false, _) => {}
        }
    }
    let n = found.len();
    (n >= 2).then(|| (found[n - 2].clone(), found[n - 1].clone()))
}

#[test]
fn no_refusal_of_a_limit_is_built_without_the_limits_helper() {
    let mut bare = Vec::new();
    for (name, text) in source_files() {
        if name == "limits.rs" || name == "error.rs" {
            continue;
        }
        for (i, line) in text.lines().enumerate() {
            let rebuilt_from_a_peer = line.contains("Some(\"quota\") =>");
            if line.contains("Error::Quota(") && !line.contains("Quota(_)") && !rebuilt_from_a_peer {
                bare.push(format!("{name}:{}: {}", i + 1, line.trim()));
            }
        }
    }
    assert!(bare.is_empty(), "a Quota refusal is built by limits::refuse, which names the limit:\n{}", bare.join("\n"));
}

#[test]
fn every_refusal_names_a_setting_that_exists() {
    let files = source_files();
    let mut calls = 0;
    let mut wrong = Vec::new();
    for (name, text) in &files {
        for (i, line) in text.lines().enumerate().filter(|(_, l)| l.contains("limits::refuse(") && !l.trim_start().starts_with("//")) {
            calls += 1;
            let at = format!("{name}:{}", i + 1);
            let Some((key, file)) = last_two_literals(line) else {
                wrong.push(format!("{at}: a refusal ends with its key and its file as two literals"));
                continue;
            };
            let named_outside_a_refusal = |t: &str, wanted: &str| t.lines().any(|l| !l.contains("limits::refuse(") && l.contains(wanted));
            let holds = |wanted: &str| files.iter().any(|(_, t)| named_outside_a_refusal(t, wanted));
            let exists = if file.ends_with(".rs") {
                files.iter().any(|(n, t)| *n == file && named_outside_a_refusal(t, &key))
            } else {
                holds(&format!("\"{file}\"")) && holds(&key)
            };
            if !exists {
                wrong.push(format!("{at}: {key} is not a setting of {file}"));
            }
        }
    }
    assert!(calls >= 40, "{calls} refusals were found; the scan reads every call of limits::refuse");
    assert!(wrong.is_empty(), "a refusal names a limit its file does not hold:\n{}", wrong.join("\n"));
}
