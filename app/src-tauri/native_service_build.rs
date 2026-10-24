//! Compile the real passive observer into the app; never starts an executable.
use std::collections::BTreeSet;
use std::env;
use std::fs;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

fn swift(arguments: &[String]) -> Output {
    let mut command = Command::new("/usr/bin/swiftc");
    for (name, _) in env::vars_os() {
        let key = name.to_string_lossy();
        if key.starts_with("DYLD_") || key.starts_with("LD_")
            || matches!(key.as_ref(), "SWIFT_EXEC" | "SWIFTFLAGS" | "CODESIGN_ALLOCATE"
                | "DEVELOPER_DIR" | "SDKROOT" | "SWIFT_MODULECACHE_PATH" | "CLANG_MODULE_CACHE_PATH") {
            command.env_remove(name);
        }
    }
    let output = command.args(arguments).output().expect("could not run the installed Swift compiler");
    assert!(output.status.success(), "passive observer compilation failed: {}", String::from_utf8_lossy(&output.stderr));
    output
}

fn runtime_directory(text: &str) -> PathBuf {
    assert!(!text.contains(['\n', '\r', '\0']), "invalid Swift runtime directory");
    let path = PathBuf::from(text);
    assert!(path.is_absolute(), "Swift runtime directory must be absolute");
    let canonical = path.canonicalize().expect("Swift runtime directory is unavailable");
    let metadata = fs::metadata(&canonical).expect("Swift runtime directory cannot be inspected");
    assert!(canonical == path && metadata.is_dir() && metadata.uid() == 0 && metadata.mode() & 0o002 == 0,
            "Swift runtime directory is not a system toolchain input");
    canonical
}

pub fn build() {
    let app = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").expect("missing app build root"));
    let output = PathBuf::from(env::var_os("OUT_DIR").expect("missing app build output"));
    let config: serde_json::Value = serde_json::from_slice(&fs::read(app.join("tauri.conf.json")).unwrap()).unwrap();
    let minimum = config["bundle"]["macOS"]["minimumSystemVersion"].as_str().expect("missing macOS deployment floor");
    assert!(minimum.chars().all(|c| c.is_ascii_digit() || c == '.') && minimum.chars().any(|c| c.is_ascii_digit()),
            "invalid macOS deployment floor");
    let architecture = match env::var("CARGO_CFG_TARGET_ARCH").as_deref() {
        Ok("aarch64") => "arm64", Ok("x86_64") => "x86_64", _ => panic!("unsupported macOS app architecture"),
    };
    let target = format!("{architecture}-apple-macosx{minimum}");
    let information: serde_json::Value = serde_json::from_slice(&swift(&[
        "-target".into(), target.clone(), "-print-target-info".into(),
    ]).stdout).expect("invalid Swift target metadata");
    let mut runtime_paths = BTreeSet::new();
    for path in information["paths"]["runtimeLibraryPaths"].as_array().expect("missing Swift runtime paths") {
        runtime_paths.insert(runtime_directory(path.as_str().expect("invalid Swift runtime path")));
    }
    let source = app.join("../native-service");
    let header = source.join("generated/PassiveServiceData.h");
    let files = [source.join("Inspection.swift"), source.join("Bridge.swift"),
                 source.join("generated/PassiveServiceData.swift")];
    let mut arguments = vec!["-j".into(), "1".into(), "-parse-as-library".into(), "-emit-library".into(),
        "-static".into(), "-module-name".into(), "PoolhousePassiveService".into(),
        "-target".into(), target, "-module-cache-path".into(), output.join("swift-modules").display().to_string(),
        "-import-objc-header".into(), header.display().to_string()];
    arguments.extend(files.iter().map(|p| p.display().to_string()));
    arguments.extend(["-o".into(), output.join("libpoolhouse_passive_service.a").display().to_string()]);
    swift(&arguments);
    println!("cargo:rerun-if-changed={}", app.join("tauri.conf.json").display());
    for path in files.iter().chain(std::iter::once(&header)) {
        println!("cargo:rerun-if-changed={}", path.display());
    }
    println!("cargo:rustc-link-search=native={}", output.display());
    for path in runtime_paths { println!("cargo:rustc-link-search=native={}", path.display()); }
    println!("cargo:rustc-link-lib=static=poolhouse_passive_service");
    for library in information["target"]["compatibilityLibraries"].as_array().expect("missing Swift compatibility metadata") {
        let name = library["libraryName"].as_str().expect("invalid Swift compatibility library");
        assert!(name.starts_with("swiftCompatibility") && name.chars().all(|c| c.is_ascii_alphanumeric()),
                "unsupported Swift compatibility library");
        println!("cargo:rustc-link-lib=static={name}");
    }
    for framework in ["Foundation", "ServiceManagement"] {
        println!("cargo:rustc-link-lib=framework={framework}");
    }
    if information["target"]["librariesRequireRPath"].as_bool() == Some(true) {
        // System runtime only: never embed a developer-account/toolchain path in the app.
        println!("cargo:rustc-link-arg=-Wl,-rpath,/usr/lib/swift");
    }
}
