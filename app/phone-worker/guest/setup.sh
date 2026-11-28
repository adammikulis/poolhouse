#!/bin/sh
# Run by the app inside the guest, which believes it is root for this script only (the package manager needs it).
# It runs again only when this file changes. No package here asks anyone to accept a licence.
set -eu
export DEBIAN_FRONTEND=noninteractive
RUSTUP_VERSION=1.29.3
RUSTUP_SHA256=9f608c142e0e38a78957d0661ba1dfaf0b6fb37184cfaa4b9a0aa57af6184282
RUST=1.94.0

printf 'APT::Sandbox::User "root";\nAcquire::Retries "3";\n' > /etc/apt/apt.conf.d/50phone
apt-get update
apt-get install -y --no-install-recommends python3 python3-venv git ca-certificates curl gcc libc6-dev make pkg-config procps

# the app's own user, so a program that looks it up finds a name and a home
grep -q "^worker:" /etc/passwd || echo "worker:x:$APP_UID:$APP_UID::/work/home:/bin/sh" >> /etc/passwd
grep -q "^worker:" /etc/group || echo "worker:x:$APP_UID:" >> /etc/group

[ -x /opt/venv/bin/python ] || python3 -m venv /opt/venv
/opt/venv/bin/pip install --disable-pip-version-check "cryptography>=50" "ladybug>=0.20.4,<0.21" "packaging>=24.2" "psutil>=7.2.2" \
    "pytest>=9.1.1" "pytest-xdist>=3.8.0" "pytest-timeout>=2.3" "pyyaml>=6.0" "defusedxml>=0.7" distlib hatchling "setuptools>=68" build

if [ ! -x /opt/cargo/bin/cargo ]; then
    curl --proto '=https' --tlsv1.2 -sSf -o /tmp/rustup-init \
        "https://static.rust-lang.org/rustup/archive/$RUSTUP_VERSION/aarch64-unknown-linux-gnu/rustup-init"
    echo "$RUSTUP_SHA256  /tmp/rustup-init" | sha256sum -c -
    chmod 700 /tmp/rustup-init
    RUSTUP_HOME=/opt/rustup CARGO_HOME=/opt/cargo /tmp/rustup-init -y --no-modify-path --profile minimal --default-toolchain "$RUST"
    rm /tmp/rustup-init
fi
