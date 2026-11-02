#!/bin/sh
# The explicit repository installer shares ownership and scope checks with Python.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "${PYTHON:-python3}" "$here/install-hooks.py" "$@"
