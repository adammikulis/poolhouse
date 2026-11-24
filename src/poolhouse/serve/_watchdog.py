"""Run as a script: stop a server's process tree when the process that started it is gone."""

from __future__ import annotations

import sys
import time

import psutil

from poolhouse.lock import pid_alive

POLL_S = 0.5
TOLERANCE_S = 2.0
GRACE_S = 5.0


def stop_tree(pid: int) -> None:
    """Terminate ``pid`` and its descendants, then kill whatever outlasts the grace."""
    try:
        parent = psutil.Process(pid)
        victims = [*parent.children(recursive=True), parent]
    except psutil.Error:
        return
    for proc in victims:
        try:
            proc.terminate()
        except psutil.Error:
            continue
    _gone, left = psutil.wait_procs(victims, timeout=GRACE_S)
    for proc in left:
        try:
            proc.kill()
        except psutil.Error:
            continue


def watch(argv: list[str]) -> int:
    """``parent_pid parent_created child_pid child_created``; returns when the child is gone."""
    parent, parent_created, child, child_created = int(argv[0]), float(argv[1]), int(argv[2]), \
        float(argv[3])
    while pid_alive(child, child_created, TOLERANCE_S):
        if not pid_alive(parent, parent_created, TOLERANCE_S):
            stop_tree(child)
            return 0
        time.sleep(POLL_S)
    return 0


if __name__ == "__main__":
    sys.exit(watch(sys.argv[1:]))
