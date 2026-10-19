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

/// The index just past the end of the string, character or comment that starts at ``at`` in ``text``, when one does.
fn skip_literal(text: &str, at: usize) -> Option<usize> {
    let rest = &text[at..];
    let bytes = rest.as_bytes();
    if rest.starts_with("//") {
        return Some(at + rest.find('\n').unwrap_or(rest.len()));
    }
    if rest.starts_with("/*") {
        return Some(at + rest.find("*/").map_or(rest.len(), |i| i + 2));
    }
    let raw = bytes[0] == b'r' && rest[1..].trim_start_matches('#').starts_with('"');
    if raw {
        let hashes = rest[1..].bytes().take_while(|b| *b == b'#').count();
        let close = format!("\"{}", "#".repeat(hashes));
        return Some(at + rest[1 + hashes + 1..].find(&close).map_or(rest.len(), |i| 1 + hashes + 1 + i + close.len()));
    }
    if bytes[0] == b'"' {
        let (mut i, mut escaped) = (1, false);
        while i < bytes.len() {
            match (escaped, bytes[i]) {
                (true, _) => escaped = false,
                (false, b'\\') => escaped = true,
                (false, b'"') => return Some(at + i + 1),
                _ => {}
            }
            i += 1;
        }
        return Some(text.len());
    }
    if bytes[0] == b'\'' {
        let close = if bytes.get(1) == Some(&b'\\') { rest[2..].find('\'').map(|i| i + 3) } else { rest[1..].chars().next().filter(|_| rest[1..].chars().nth(1) == Some('\'')).map(|c| c.len_utf8() + 2) };
        return close.map(|n| at + n);
    }
    None
}

/// The index of the brace that closes the one at ``open`` in ``text``, skipping strings, characters and comments.
fn closing_brace(text: &str, open: usize) -> usize {
    let (mut depth, mut at) = (0usize, open);
    while at < text.len() {
        if let Some(past) = skip_literal(text, at) {
            at = past;
            continue;
        }
        match text.as_bytes()[at] {
            b'{' => depth += 1,
            b'}' => {
                depth -= 1;
                if depth == 0 {
                    return at;
                }
            }
            _ => {}
        }
        at += text[at..].chars().next().map_or(1, char::len_utf8);
    }
    panic!("an unbalanced block at {}", &text[open..(open + 60).min(text.len())]);
}

/// The source ``text`` with each test module (`#[cfg(test)]` or `#[cfg(all(test, ...))]` on a `mod` with a body) blanked out, its newlines kept so
/// the lines of what is left are the lines of the file. Anything else under a test attribute, and a `cfg(not(test))` module, stays.
fn product(text: &str) -> String {
    let mut out = text.to_string();
    for (at, _) in text.match_indices("#[cfg(") {
        let attribute = balanced(text, at + "#[cfg".len());
        let word = |w: &str| !mentions(attribute, w).is_empty();
        if !word("test") || word("not") {
            continue;
        }
        let after = &text[at + "#[cfg".len() + attribute.len() + 2..];
        let skipped = after.trim_start().strip_prefix(']').unwrap_or(after).trim_start();
        let Some(rest) = skipped.strip_prefix("mod ").or_else(|| skipped.strip_prefix("pub mod ")) else { continue };
        let Some(brace) = rest.find('{').filter(|i| !rest[..*i].contains(';')) else { continue };
        let open = text.len() - rest.len() + brace;
        let close = closing_brace(text, open);
        let blank: String = text[at..=close].chars().map(|c| if c == '\n' { "\n".to_string() } else { " ".repeat(c.len_utf8()) }).collect();
        out.replace_range(at..=close, &blank);
    }
    out
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
            found.push((file.clone(), line_of(&text, at), arguments(balanced(&text, at + name.len() - 1))));
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
    assert_eq!(mentions(&product(&limits), "Quota").len(), 3, "and once in each");
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

/// ``text`` with its comments, strings and characters blanked out (newlines and length kept), so a word is looked for in code alone.
fn code_only(text: &str) -> String {
    let (mut out, mut at) = (String::new(), 0);
    while at < text.len() {
        match skip_literal(text, at) {
            Some(past) => {
                out.extend(text[at..past].chars().map(|c| if c == '\n' { '\n' } else { ' ' }));
                at = past;
            }
            None => {
                let c = text[at..].chars().next().unwrap();
                out.push(c);
                at += c.len_utf8();
            }
        }
    }
    out
}

/// Where the code of the files names a helper of `limits` without the path `limits::`, or imports one.
fn helper_findings(files: &[(String, String)]) -> Vec<String> {
    let mut wrong = Vec::new();
    for (name, text) in files {
        if name == "limits.rs" {
            continue;
        }
        let code = code_only(&product(text));
        for helper in ["floor", "refuse", "relayed"] {
            let own = mentions(&code, "fn").iter().any(|(_, rest)| rest.trim_start().strip_prefix(helper).is_some_and(|r| r.starts_with('(')));
            for (at, rest) in mentions(&code, helper).into_iter().filter(|_| !own) {
                let before = code[..at].trim_end();
                let called = rest.starts_with('(');
                if called && !(before.ends_with("limits::") || before.ends_with('.') || before.ends_with("fn")) {
                    wrong.push(format!("{name}:{}: {helper}( is called without the path limits::", line_of(&code, at)));
                }
            }
        }
        for (at, _) in mentions(&code, "use") {
            let statement = &code[at..at + code[at..].find(';').unwrap_or(code.len() - at)];
            let imports = statement.contains("limits") && (statement.contains('*') || ["floor", "refuse", "relayed"].iter().any(|h| !mentions(statement, h).is_empty()));
            if imports {
                wrong.push(format!("{name}:{}: a helper of limits is imported, so its calls would not be found: {}", line_of(&code, at), statement.split_whitespace().collect::<Vec<_>>().join(" ")));
            }
        }
    }
    wrong
}

/// Where a file spells the code `quota` other than as the pattern that passes on what another node said.
fn quota_code_findings(files: &[(String, String)]) -> Vec<String> {
    let mut wrong = Vec::new();
    for (name, text) in files {
        if name == "error.rs" || name == "limits.rs" {
            continue;
        }
        for (i, line) in text.lines().enumerate().filter(|(_, l)| !l.trim_start().starts_with("//")) {
            if line.contains("\"quota\"") && !line.contains("Some(\"quota\") => crate::limits::relayed(") {
                wrong.push(format!("{name}:{}: the code quota is built or matched other than by relaying another node's refusal: {}", i + 1, line.trim()));
            }
        }
    }
    wrong
}

/// Where a floor refusal is anything but one plain sentence with no number in it.
fn floor_findings(files: &[(String, String)]) -> Vec<String> {
    let mut wrong = Vec::new();
    for (file, line, args) in calls(files, "limits::floor(") {
        let message = args.first().cloned().unwrap_or_default();
        let plain = args.len() == 1 && literal(&message).is_some_and(|s| !s.contains(['{', '}']) && !s.chars().any(|c| c.is_ascii_digit()));
        if !plain {
            wrong.push(format!("{file}:{line}: a floor refusal is one string literal with no number and no interpolation in it: {message}"));
        }
    }
    wrong
}

/// Where a limit refusal does not name its limit, a setting its file holds and the document `docs` lists.
fn refuse_findings(files: &[(String, String)], docs: &str) -> Vec<String> {
    let mut wrong = Vec::new();
    for (name, line, args) in calls(files, "limits::refuse(") {
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
    wrong
}

fn limits_document() -> String {
    std::fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("../../docs/limits.md")).unwrap()
}

#[test]
fn a_floor_refusal_states_no_budget() {
    let files = source_files();
    let floors = calls(&files, "limits::floor(");
    assert!(floors.len() >= 14, "{} floor refusals were found; the scan reads every call of limits::floor", floors.len());
    let wrong = floor_findings(&files);
    assert!(wrong.is_empty(), "{}", wrong.join("\n"));
}

#[test]
fn every_refusal_names_a_setting_that_exists_and_docs_limits_lists_it() {
    let files = source_files();
    let refusals = calls(&files, "limits::refuse(");
    assert!(refusals.len() >= 35, "{} refusals were found; the scan reads every call of limits::refuse", refusals.len());
    let wrong = refuse_findings(&files, &limits_document());
    assert!(wrong.is_empty(), "a refusal names a limit its file does not hold or the document leaves out:\n{}", wrong.join("\n"));
}

#[test]
fn the_helpers_are_called_by_their_path_and_the_code_quota_is_only_relayed() {
    let files = source_files();
    let wrong: Vec<String> = helper_findings(&files).into_iter().chain(quota_code_findings(&files)).collect();
    assert!(wrong.is_empty(), "{}", wrong.join("\n"));
}

fn one(name: &str, text: &str) -> Vec<(String, String)> {
    vec![(name.to_string(), text.to_string()), ("things.rs".to_string(), "pub const MOST_THINGS: usize = 3;\n".to_string())]
}

#[test]
fn a_floor_with_a_number_or_an_interpolated_or_computed_sentence_is_found() {
    for sentence in ["floor(&format!(\"{MOST_TRIES} tries, wait\"))", "floor(\"tries: {MOST_TRIES}\")", "floor(\"Try 3 times\")", "floor(&said)", "floor(\"a\", 3)"] {
        let source = format!("fn f() -> Error {{\n    crate::limits::{sentence}\n}}\n");
        assert_eq!(floor_findings(&one("a.rs", &source)).len(), 1, "{sentence}");
    }
    assert!(floor_findings(&one("a.rs", "fn f() { crate::limits::floor(\"Wait a little; then ask again.\") }\n")).is_empty());
}

#[test]
fn a_helper_imported_or_called_by_a_name_that_hides_its_path_is_found() {
    for source in ["use crate::limits::floor;\nfn f() { floor(\"x\") }\n", "use crate::limits::{refuse, Rates};\n", "use crate::limits::*;\n", "use crate::limits as l;\nfn f() { l::floor(\"x\") }\n",
                   "use crate::limits::relayed as passed;\n", "fn f() { refuse(\"x\", 1, \"K\", \"a.rs\") }\n"] {
        assert!(!helper_findings(&one("a.rs", source)).is_empty(), "{source}");
    }
    for source in ["use crate::limits::Rates;\n", "fn f() { crate::limits::floor(\"x\") }\n", "fn f() { 2.5_f64.floor(); }\n", "// floor(\"x\") is a floor\nfn f() { let s = \"floor(1)\"; }\n"] {
        assert!(helper_findings(&one("a.rs", source)).is_empty(), "{source}");
    }
}

#[test]
fn the_code_quota_built_by_say_or_by_hand_is_found_and_relaying_is_not() {
    for source in ["fn f() -> Error { Error::say(\"quota\", \"x\", \"none\", \"stop\") }\n", "fn f(c: &str) -> bool { c == \"quota\" }\n"] {
        assert_eq!(quota_code_findings(&one("a.rs", source)).len(), 1, "{source}");
    }
    assert!(quota_code_findings(&one("a.rs", "match code { Some(\"quota\") => crate::limits::relayed(message), _ => other }\n")).is_empty());
}

#[test]
fn a_refusal_after_a_test_attribute_that_is_not_a_test_module_is_scanned_and_one_inside_a_test_module_is_not() {
    let bad = "crate::limits::refuse(\"x\", 1, \"NO_SUCH_LIMIT\", \"things.rs\")";
    let docs = "`MOST_THINGS`";
    let after_helper = format!("#[cfg(test)]\nfn helper() {{}}\n\nfn f() -> Error {{ {bad} }}\n");
    let after_use = format!("#[cfg(test)]\nuse std::fmt;\n\nfn f() -> Error {{ {bad} }}\n");
    let not_test = format!("#[cfg(not(test))]\nmod real {{\n    fn f() -> Error {{ {bad} }}\n}}\n");
    let after_module = format!("#[cfg(test)]\nmod tests {{\n    fn a() {{ let s = \"}}\"; let c = '{{'; }}\n}}\n\nfn f() -> Error {{ {bad} }}\n");
    for source in [after_helper, after_use, not_test, after_module] {
        let found = refuse_findings(&one("a.rs", &source), docs);
        assert!(found.iter().any(|f| f.contains("NO_SUCH_LIMIT is not a constant")), "{source}: {found:?}");
    }
    let inside = format!("fn g() {{}}\n\n#[cfg(all(test, unix))]\nmod tests {{\n    fn a() -> Error {{ {bad} }}\n}}\n");
    assert!(refuse_findings(&one("a.rs", &inside), docs).is_empty(), "a refusal inside a test module is not product code");
    let good = "fn f() -> Error { crate::limits::refuse(\"x\", 3, \"MOST_THINGS\", \"things.rs\") }\n";
    assert!(refuse_findings(&one("a.rs", good), docs).is_empty());
    assert_eq!(refuse_findings(&one("a.rs", good), "").len(), 1, "a limit the document leaves out is found");
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
