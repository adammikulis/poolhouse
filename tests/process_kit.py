"""Programs that make a confined process start many processes or hold many threads."""


FORKS = r"""
import errno, json, os, sys, time
wanted, forked, refused = int(sys.argv[1]), 0, None
for _ in range(wanted):
    try:
        pid = os.fork()
    except OSError as error:
        refused = error.errno
        break
    if pid == 0:
        time.sleep(3)
        os._exit(0)
    forked += 1
print(json.dumps({"forked": forked, "refused": refused, "again": errno.EAGAIN}))
sys.stdout.flush()
os._exit(0)
"""
"""A program that forks until the system says no, and reports how many it made and the error."""

THREADS = r"""
import sys, threading, time
for _ in range(int(sys.argv[1])):
    threading.Thread(target=time.sleep, args=(120,), daemon=True).start()
print("ready", flush=True)
time.sleep(120)
"""
"""A program that holds many threads open, as a desktop user's processes do."""
