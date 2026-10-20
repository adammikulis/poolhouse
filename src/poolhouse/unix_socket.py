"""Exact live process identity of a connected local Unix stream, never its ancestry.

Callers use a fresh accepted/connected pathname socket and recheck ``matches``
before and after every bounded frame. Descriptor inheritance does not establish
who currently writes it; this helper cannot prove the caller's accept lifecycle.
"""
from __future__ import annotations

import ctypes
import math
import os
import socket
import struct
import sys

import psutil


class IdentityUnavailable(OSError):
    """The kernel endpoint or its current process generation cannot be established."""


def _credentials(channel):
    if sys.platform == 'darwin':
        raw = channel.getsockopt(0, 2, struct.calcsize('i'))  # SOL_LOCAL, LOCAL_PEERPID
        if len(raw) != struct.calcsize('i'):
            raise IdentityUnavailable('Invalid Unix endpoint credentials')
        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        getpeereid = ctypes.CDLL(None, use_errno=True).getpeereid
        getpeereid.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        getpeereid.restype = ctypes.c_int
        if getpeereid(channel.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
            raise IdentityUnavailable('Unix endpoint credentials are unavailable')
        return struct.unpack('i', raw)[0], uid.value
    if sys.platform == 'linux':
        raw = channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('iII'))
        if len(raw) != struct.calcsize('iII'):
            raise IdentityUnavailable('Invalid Unix endpoint credentials')
        pid, uid, _gid = struct.unpack('iII', raw)
        return pid, uid
    raise IdentityUnavailable('Unix process credentials are unsupported')


def _started(pid):
    process = psutil.Process(pid)
    if process.uids().effective != os.geteuid() or process.status() == psutil.STATUS_ZOMBIE:
        raise IdentityUnavailable('Unix endpoint process is unavailable')
    started = process.create_time()
    if not math.isfinite(started) or started <= 0:
        raise IdentityUnavailable('Unix endpoint process generation is unavailable')
    return started


def identity(channel):
    """Return kernel endpoint PID/birth time; deny unknown, foreign or unstable endpoints."""
    try:
        if (channel.family != socket.AF_UNIX
                or channel.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM
                or not (channel.getsockname() or channel.getpeername())):
            raise IdentityUnavailable('A connected pathname Unix stream is required')
        # Always prove connection even when the local endpoint has a pathname.
        channel.getpeername()
        pid, uid = _credentials(channel)
        if pid <= 0 or uid != os.geteuid():
            raise IdentityUnavailable('Unix endpoint process is unavailable')
        started = _started(pid)
        if _credentials(channel) != (pid, uid) or _started(pid) != started:
            raise IdentityUnavailable('Unix endpoint process changed')
        return {'pid': pid, 'started': started}
    except (OSError, ValueError, AttributeError, psutil.Error) as exc:
        raise IdentityUnavailable('Unix endpoint identity is unavailable') from exc


def matches(channel, expected):
    """Check an exact pinned process generation, failing closed on any uncertainty."""
    if (not isinstance(expected, dict) or set(expected) != {'pid', 'started'}
            or type(expected['pid']) is not int or expected['pid'] <= 0
            or type(expected['started']) not in (int, float)
            or not math.isfinite(expected['started']) or expected['started'] <= 0):
        return False
    try:
        return identity(channel) == expected
    except IdentityUnavailable:
        return False
