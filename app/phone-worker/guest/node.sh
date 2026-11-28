#!/bin/sh
# The phone's node, from the phone's own working copy (/work/repo) and the binary built there (/work/poolhouse-node).
# Run by the app inside the guest as its own unprivileged user.
set -eu
. /opt/worker/env.sh
if [ ! -d /work/repo/src ] || [ ! -x /work/poolhouse-node ]; then
    echo "no working copy at /work/repo or no node binary at /work/poolhouse-node yet"
    exit 3
fi
cd /work/repo
exec python /opt/worker/node.py
