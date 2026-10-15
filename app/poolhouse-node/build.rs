fn main() {
    println!("cargo:rerun-if-env-changed=POOLHOUSE_PHONE_SIGNER_SHA256");
    println!("cargo:rerun-if-env-changed=POOLHOUSE_PHONE_MIN_VERSION_CODE");
}
