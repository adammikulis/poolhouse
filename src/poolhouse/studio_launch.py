"""Bounded private launch messages consumed by a fixed Studio child process."""
from __future__ import annotations

import copy
import json
import math
import os
import socket
import time

from poolhouse.json_objects import unique_object

FD_ENV = 'POOLHOUSE_STUDIO_LAUNCH_FD'
MAX_FRAME = 16 * 1024
DEADLINE = 10.0


class LaunchInterrupted(OSError):
    """The launch did not produce a proven admission; it must not be retried blindly."""


def send_frame(channel, value):
    encoded = json.dumps(value, allow_nan=False, separators=(',', ':')).encode()
    if len(encoded) > MAX_FRAME:
        raise LaunchInterrupted('Studio launch frame exceeds its bound')
    channel.sendall(len(encoded).to_bytes(4, 'big') + encoded)


def receive_frame(channel):
    deadline = time.monotonic() + (channel.gettimeout() or DEADLINE)
    def exact(size):
        result = bytearray()
        while len(result) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LaunchInterrupted('Studio launch admission expired')
            channel.settimeout(remaining)
            chunk = channel.recv(size - len(result))
            if not chunk:
                raise LaunchInterrupted('Studio launch admission disconnected')
            result.extend(chunk)
        return bytes(result)
    size = int.from_bytes(exact(4), 'big')
    if size > MAX_FRAME:
        raise LaunchInterrupted('Studio launch frame exceeds its bound')
    try:
        result = json.loads(exact(size), object_pairs_hook=unique_object)
    except (ValueError, UnicodeError) as exc:
        raise LaunchInterrupted('Invalid Studio launch admission') from exc
    if not isinstance(result, dict):
        raise LaunchInterrupted('Invalid Studio launch admission')
    return result


def validate_context(value):
    fields = {'permit_id', 'request_id', 'run_id', 'node_id', 'request_digest', 'model_digest', 'calibration_digest', 'broker'}
    if not isinstance(value, dict) or set(value) != fields:
        raise LaunchInterrupted('Studio launch context is unavailable')
    for key in fields - {'broker'}:
        if not isinstance(value[key], str) or not value[key] or len(value[key]) > 256:
            raise LaunchInterrupted('Invalid Studio launch identity')
    for key in ('request_digest', 'model_digest', 'calibration_digest'):
        if len(value[key]) != 64 or any(char not in '0123456789abcdef' for char in value[key]):
            raise LaunchInterrupted('Invalid Studio launch content identity')
    broker = value['broker']
    if (not isinstance(broker, dict) or set(broker) != {'pid', 'started', 'instance', 'port'}
            or type(broker['pid']) is not int or broker['pid'] <= 0
            or type(broker['port']) is not int or not 0 < broker['port'] < 65536
            or type(broker['started']) not in (float, int) or not math.isfinite(broker['started'])
            or broker['started'] <= 0 or not isinstance(broker['instance'], str)
            or not broker['instance'] or len(broker['instance']) > 256):
        raise LaunchInterrupted('Invalid Studio broker identity')
    return copy.deepcopy(value)


def worker_context():
    """Block the fixed worker until its actual parent launch is registered and acknowledged."""
    raw = os.environ.pop(FD_ENV, '')
    if not raw.isdecimal():
        raise LaunchInterrupted('Studio worker requires private launch admission')
    try:
        with socket.socket(fileno=int(raw)) as channel:
            expires = time.monotonic() + DEADLINE
            channel.settimeout(DEADLINE)
            offer = receive_frame(channel)
            if (set(offer) != {'challenge', 'job_id', 'context'}
                    or not isinstance(offer['challenge'], str) or len(offer['challenge']) != 43
                    or not isinstance(offer['job_id'], str) or not offer['job_id']):
                raise LaunchInterrupted('Invalid Studio worker launch admission')
            context = validate_context(offer['context'])
            send_frame(channel, {'challenge': offer['challenge'], 'pid': os.getpid()})
            remaining = expires - time.monotonic()
            if remaining <= 0:
                raise LaunchInterrupted('Studio launch admission expired')
            channel.settimeout(remaining)
            if receive_frame(channel) != {'ack': offer['challenge']}:
                raise LaunchInterrupted('Studio worker launch was not acknowledged')
            return context
    except (OSError, ValueError) as exc:
        raise LaunchInterrupted('Studio worker launch admission interrupted') from exc
