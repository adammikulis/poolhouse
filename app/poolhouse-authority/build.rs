fn main() {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("macos") { return; }
    let sources = ["../native-host/NativeIdentity.c", "../native-host/NativeCaller.c", "../native-host/MachCode.c",
        "../native-host/MachSender.c", "../native-host/AuthorityChannel.c"];
    let headers = ["../native-host/NativeIdentity.h", "../native-host/NativeCaller.h", "../native-host/MachCode.h",
        "../native-host/MachSender.h", "../native-host/NativeRequest.h", "../native-host/AuthorityChannel.h"];
    let mut build = cc::Build::new();
    build.std("c11").flag("-fblocks").warnings(true).warnings_into_errors(true);
    for source in sources { println!("cargo:rerun-if-changed={source}"); build.file(source); }
    for header in headers { println!("cargo:rerun-if-changed={header}"); }
    build.compile("poolhouse_authority_channel");
    println!("cargo:rustc-link-lib=framework=Security");
    println!("cargo:rustc-link-lib=framework=CoreFoundation");
    println!("cargo:rustc-link-lib=bsm");
}
