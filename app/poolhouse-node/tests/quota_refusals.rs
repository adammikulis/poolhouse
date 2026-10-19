//! Every quota refusal is built by one of three helpers of `limits.rs`: `refuse` names the limit, its value and the setting that holds
//! it, `floor` states no budget (a security floor), `relayed` passes on what another node said. The setting a `refuse` names exists
//! and is listed in docs/limits.md.

use std::collections::BTreeSet;
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

fn word_at(text: &str, at: usize, word: &str) -> bool {
    let ident = |b: u8| b.is_ascii_alphanumeric() || b == b'_';
    let bytes = text.as_bytes();
    text[at..].starts_with(word) && (at == 0 || !ident(bytes[at - 1])) && bytes.get(at + word.len()).is_none_or(|b| !ident(*b))
}

/// Every place the word ``word`` stands in ``text``, with the text that follows it.
fn mentions<'a>(text: &'a str, word: &str) -> Vec<(usize, &'a str)> {
    text.match_indices(word).map(|(at, _)| at).filter(|at| word_at(text, *at, word)).map(|at| (at, &text[at + word.len()..])).collect()
}

fn line_of(text: &str, at: usize) -> usize {
    text[..at].matches('\n').count() + 1
}

/// Whether what follows `Quota` is a pattern that holds no value: `(_)`.
fn is_empty_pattern(rest: &str) -> bool {
    rest.trim_start().strip_prefix('(').is_some_and(|r| r.trim_start().starts_with("_)"))
}

/// The part of a source file before its test module.
fn product(text: &str) -> &str {
    text.find("#[cfg(test)]").map_or(text, |at| &text[..at])
}

/// The text of the parentheses that open at ``open`` (the index of the `(`).
fn balanced(text: &str, open: usize) -> &str {
    let (mut depth, mut quoted, mut escaped) = (0, false, false);
    for (i, c) in text[open..].char_indices() {
        if quoted {
            match (escaped, c) {
                (true, _) => escaped = false,
                (false, '\\') => escaped = true,
                (false, '"') => quoted = false,
                _ => {}
            }
            continue;
        }
        match c {
            '"' => quoted = true,
            '(' => depth += 1,
            ')' => {
                depth -= 1;
                if depth == 0 {
                    return &text[open + 1..open + i];
                }
            }
            _ => {}
        }
    }
    panic!("an unbalanced call at {}", &text[open..(open + 60).min(text.len())]);
}

/// ``args`` split at the commas outside any bracket or string.
fn arguments(args: &str) -> Vec<String> {
    let (mut out, mut cur, mut depth, mut quoted, mut escaped) = (Vec::new(), String::new(), 0, false, false);
    for c in args.chars() {
        if quoted {
            cur.push(c);
            match (escaped, c) {
                (true, _) => escaped = false,
                (false, '\\') => escaped = true,
                (false, '"') => quoted = false,
                _ => {}
            }
            continue;
        }
        match c {
            '"' => {
                quoted = true;
                cur.push(c);
            }
            '(' | '[' | '{' => {
                depth += 1;
                cur.push(c);
            }
            ')' | ']' | '}' => {
                depth -= 1;
                cur.push(c);
            }
            ',' if depth == 0 => out.push(std::mem::take(&mut cur).trim().to_string()),
            _ => cur.push(c),
        }
    }
    if !cur.trim().is_empty() {
        out.push(cur.trim().to_string());
    }
    out
}

fn literal(arg: &str) -> Option<&str> {
    arg.strip_prefix('"').and_then(|a| a.strip_suffix('"'))
}

/// Every call of ``name`` (`limits::refuse(`) in the product code of the files, as (file, line, arguments).
fn calls(files: &[(String, String)], name: &str) -> Vec<(String, usize, Vec<String>)> {
    let mut found = Vec::new();
    for (file, text) in files {
        let text = product(text);
        for (at, _) in text.match_indices(name) {
            let line_start = text[..at].rfind('\n').map_or(0, |i| i + 1);
            if text[line_start..at].trim_start().starts_with("//") {
                continue;
            }
            found.push((file.clone(), line_of(text, at), arguments(balanced(text, at + name.len() - 1))));
        }
    }
    found
}

#[test]
fn a_quota_refusal_is_built_by_a_limits_helper_and_nowhere_else() {
    let mut wrong = Vec::new();
    for (name, text) in source_files() {
        if name == "error.rs" || name == "limits.rs" {
            continue;
        }
        for (at, rest) in mentions(&text, "Quota") {
            if !is_empty_pattern(rest) {
                wrong.push(format!("{name}:{}: Quota is named other than as the pattern `Quota(_)`", line_of(&text, at)));
            }
        }
    }
    assert!(wrong.is_empty(), "a Quota refusal is built by limits::refuse, floor or relayed:\n{}", wrong.join("\n"));
}

#[test]
fn only_the_three_helpers_of_limits_build_a_quota_and_error_names_the_variant_and_nothing_more() {
    let files = source_files();
    let text = |name: &str| files.iter().find(|(n, _)| n == name).map(|(_, t)| t.clone()).unwrap();
    let limits = text("limits.rs");
    let mut owners = BTreeSet::new();
    for chunk in product(&limits).split("\npub fn ").skip(1) {
        if chunk.contains("Quota(") {
            owners.insert(chunk.split('(').next().unwrap().to_string());
        }
    }
    assert_eq!(owners.into_iter().collect::<Vec<_>>(), ["floor", "refuse", "relayed"], "limits.rs builds a Quota in these three functions only");
    assert_eq!(mentions(product(&limits), "Quota").len(), 3, "and once in each");
    let error = text("error.rs");
    assert!(!error.contains("macro_rules!"), "error.rs defines no macro");
    for (at, _) in mentions(&error, "Quota") {
        let line = error.lines().nth(line_of(&error, at) - 1).unwrap();
        let allowed = line.contains("Quota(String)") || line.contains("Error::Quota(_) =>") || line.contains("| Error::Quota(m) |");
        assert!(allowed, "error.rs:{}: {line}", line_of(&error, at));
    }
    for (name, source) in &files {
        assert!(!(source.contains("macro_rules!") && source.contains("Quota")), "{name} defines a macro beside a Quota");
    }
}

#[test]
fn a_floor_refusal_states_no_budget() {
    let files = source_files();
    let floors = calls(&files, "limits::floor(");
    assert!(floors.len() >= 14, "{} floor refusals were found; the scan reads every call of limits::floor", floors.len());
    let mut wrong = Vec::new();
    for (file, line, args) in &floors {
        let message = args.first().cloned().unwrap_or_default();
        let sentence: String = message.split('"').skip(1).step_by(2).collect();
        if args.len() != 1 || sentence.chars().any(|c| c.is_ascii_digit()) {
            wrong.push(format!("{file}:{line}: a floor refusal takes its sentence alone, and the sentence holds no number: {message}"));
        }
    }
    assert!(wrong.is_empty(), "{}", wrong.join("\n"));
}

#[test]
fn every_refusal_names_a_setting_that_exists_and_docs_limits_lists_it() {
    let files = source_files();
    let refusals = calls(&files, "limits::refuse(");
    assert!(refusals.len() >= 35, "{} refusals were found; the scan reads every call of limits::refuse", refusals.len());
    let docs = std::fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("../../docs/limits.md")).unwrap();
    let mut wrong = Vec::new();
    for (name, line, args) in &refusals {
        let at = format!("{name}:{line}");
        let (Some(key), Some(file)) = (args.get(2).and_then(|a| literal(a)), args.get(3).and_then(|a| literal(a))) else {
            wrong.push(format!("{at}: a refusal is its sentence, its value, a key and a file, the last two literals: {args:?}"));
            continue;
        };
        let declared = |text: &str, form: &str| text.lines().any(|l| l.trim_start().starts_with(form) || l.contains(&format!(" {form}")));
        let exists = if file.ends_with(".rs") {
            files.iter().any(|(n, t)| n == file && (declared(t, &format!("const {key}:")) || declared(t, &format!("static {key}:"))))
        } else {
            files.iter().any(|(_, t)| t.contains(&format!("\"{file}\""))) && files.iter().any(|(_, t)| declared(t, &format!("pub {key}:")))
        };
        if !exists {
            wrong.push(format!("{at}: {key} is not a constant of {file}, nor a field of the settings in it"));
        }
        if !docs.contains(&format!("`{key}`")) {
            wrong.push(format!("{at}: docs/limits.md does not list `{key}`"));
        }
    }
    assert!(wrong.is_empty(), "a refusal names a limit its file does not hold or the document leaves out:\n{}", wrong.join("\n"));
}

#[test]
fn the_helpers_build_the_sentences_callers_are_told() {
    use poolhouse_node::error::Error;
    use poolhouse_node::limits::{floor, refuse, relayed};
    let Error::Quota(said) = refuse("a thing was refused.", 3, "MOST_THINGS", "things.rs") else { panic!() };
    assert_eq!(said, "a thing was refused (the limit is 3, set by MOST_THINGS in things.rs).");
    let Error::Quota(said) = floor("Too many tries; wait a moment") else { panic!() };
    assert_eq!(said, "Too many tries; wait a moment.");
    let Error::Quota(said) = relayed("quota: as it was said".into()) else { panic!() };
    assert_eq!(said, "quota: as it was said");
}
