#!/bin/sh
# The phone's node, from the phone's own working copy (/work/repo) and the binary built there. With no node.json it
# is a device in no pool that listens on no network. Run by the app inside the guest as its own unprivileged user.
set -eu
. /opt/worker/env.sh
/bin/sh /opt/worker/prepare.sh
cd /work/repo
exec python /opt/worker/node.py
