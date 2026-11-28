#!/bin/sh
# The phone's own working copy and node, from the source this app carries, so no computer is needed. Done again only
# when the app brings other source. Run by node.sh inside the guest; WORK and ASSETS are for the checks.
set -eu
WORK=${WORK:-/work}
ASSETS=${ASSETS:-/opt/worker}
wanted=$(cat "$ASSETS/commit")
if [ "$(cat "$WORK/source.commit" 2>/dev/null || true)" != "$wanted" ]; then
    rm -rf "$WORK/repo.new" "$WORK/source.commit" "$WORK/poolhouse-node"
    mkdir -p "$WORK/repo.new"
    tar -xzf "$ASSETS/tree.tgz" -C "$WORK/repo.new"
    git -C "$WORK/repo.new" init -q
    git -C "$WORK/repo.new" add --all --force
    git -C "$WORK/repo.new" -c user.name=phone -c user.email=phone@localhost commit -q -m "$wanted"
    rm -rf "$WORK/repo"
    mv "$WORK/repo.new" "$WORK/repo"
    echo "$wanted" > "$WORK/source.commit"
fi
if [ ! -x "$WORK/poolhouse-node" ]; then
    echo "building the node from commit $wanted"
    cd "$WORK/repo/app"
    CARGO_TARGET_DIR="$WORK/target" cargo build --locked --release -j 3 -p poolhouse-node
    cp "$WORK/target/release/poolhouse-node" "$WORK/poolhouse-node.new"
    mv "$WORK/poolhouse-node.new" "$WORK/poolhouse-node"
fi
