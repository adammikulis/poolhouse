#!/usr/bin/env bash
# Make this tree a poolhouse device inside a WSL distribution: copy it (without app/target and the history), give it a Python 3.13 environment
# and the node built from it. Run inside WSL by the pool-check workflow on a Windows runner; any Linux with curl and a C toolchain works too.
#
#     wsl_provision.sh SOURCE_DIR TARGET_DIR
set -euo pipefail

# Each phase says when it starts, so a provisioning that hangs shows where in the job's log.
phase() { echo "wsl_provision $(date +%T): $*"; }

src=$1
dst=$2
phase "copying this tree"
rm -rf "$dst"
mkdir -p "$dst"
tar -C "$src" --exclude=./app/target --exclude=./.git --exclude=./node_modules -cf - . | tar -C "$dst" -xf -
cd "$dst"

phase "installing uv and the Python environment"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
curl -LsSf https://astral.sh/uv/0.8.22/install.sh | sh
uv venv .venv --python 3.13
uv pip install --python .venv/bin/python . pytest

phase "installing Rust"
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
phase "building the node"
cargo build --manifest-path app/Cargo.toml -p poolhouse-node
test -x app/target/debug/poolhouse-node
phase "done"
