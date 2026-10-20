"""Private, generation-bound admission for actual daemon-launched Studio workers."""
from __future__ import annotations

import copy
import json
import math
import os
import secrets
import socket
import time

from poolhouse.json_objects import unique_object

from .job_records import process_started

FD_ENV = 'POOLHOUSE_STUDIO_LAUNCH_FD'
MAX_FRAME = 16 * 1024
DEADLINE = 10.0


class LaunchInterrupted(OSError):
    """The launch did not produce a proven admission; it must not be retried blindly."""


def _send(channel, value):
    encoded = json.dumps(value, allow_nan=False, separators=(',', ':')).encode()
    if len(encoded) > MAX_FRAME:
        raise LaunchInterrupted('Studio launch frame exceeds its bound')
    channel.sendall(len(encoded).to_bytes(4, 'big') + encoded)


def _receive(channel):
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


def _context(value):
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


# Only descriptor numbers appear in the wrapper command. Context and challenges never do.
WRAPPER = '''
import json, os, socket, subprocess, time
_studio_deadline = time.monotonic() + LAUNCH_TIMEOUT
_studio_report = socket.socket(fileno=REPORT_FD)
_studio_worker_fd = WORKER_FD
_launch_options = {'pass_fds': (_studio_worker_fd,), 'env': dict(os.environ, POOLHOUSE_STUDIO_LAUNCH_FD=str(_studio_worker_fd))}
def _studio_started(child):
    os.close(_studio_worker_fd)
    try:
        def remaining():
            value = _studio_deadline - time.monotonic()
            if value <= 0: raise OSError('Studio launch admission expired')
            _studio_report.settimeout(value)
        def send(body):
            remaining()
            _studio_report.sendall(len(body).to_bytes(4,'big')+body)
        def exact(size):
            data = b''
            while len(data) < size:
                remaining()
                part = _studio_report.recv(size-len(data))
                if not part: raise OSError('Studio launch admission disconnected')
                data += part
            return data
        body = json.dumps({'pid':child.pid}).encode()
        send(body)
        size = int.from_bytes(exact(4),'big')
        if size > 1024: raise OSError('Invalid Studio launch acknowledgement')
        ack = json.loads(exact(size))
        if set(ack) != {'ack'} or not isinstance(ack['ack'],str): raise OSError('Invalid Studio launch acknowledgement')
        body = json.dumps(ack).encode()
        send(body)
    except BaseException:
        try:
            child.terminate()
            child.wait(timeout=LAUNCH_TIMEOUT)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=LAUNCH_TIMEOUT)
        except ProcessLookupError:
            pass
        raise
    finally:
        _studio_report.close()
'''


class LaunchProof:
    """Own both private channels until a wrapper and its actual child acknowledge admission."""

    def __init__(self, job_id, context):
        self.job_id, self.context = job_id, _context(context)
        self.report, self.wrapper = socket.socketpair()
        self.channel, self.worker = socket.socketpair()
        self.expires = time.monotonic() + DEADLINE
        self.wrapper_pid = self.wrapper_started = self.pid = self.started = None
        self.admitted = False

    def setup(self):
        return (WRAPPER.replace('REPORT_FD', str(self.wrapper.fileno()))
                .replace('WORKER_FD', str(self.worker.fileno())).replace('LAUNCH_TIMEOUT', repr(DEADLINE)))

    @property
    def descriptors(self):
        return self.wrapper.fileno(), self.worker.fileno()

    def _deadline(self, channel):
        remaining = self.expires - time.monotonic()
        if remaining <= 0:
            raise LaunchInterrupted('Studio launch admission expired')
        channel.settimeout(remaining)

    def admit(self, wrapper, current, register):
        self.wrapper.close()
        self.worker.close()
        self.wrapper_pid, self.wrapper_started = wrapper.pid, process_started(wrapper.pid)
        self._deadline(self.report)
        reported = _receive(self.report)
        if set(reported) != {'pid'} or type(reported['pid']) is not int or reported['pid'] <= 0:
            raise LaunchInterrupted('Invalid Studio worker ownership report')
        self.pid, self.started = reported['pid'], process_started(reported['pid'])
        if not self.live() or wrapper.poll() is not None:
            raise LaunchInterrupted('Studio worker ownership changed during launch')
        challenge = secrets.token_urlsafe(32)
        self._deadline(self.channel)
        _send(self.channel, {'challenge': challenge, 'job_id': self.job_id, 'context': self.context})
        echo = _receive(self.channel)
        if echo != {'challenge': challenge, 'pid': self.pid}:
            raise LaunchInterrupted('Studio worker admission does not match its process')
        if not self.live() or current() != self.context:
            raise LaunchInterrupted('Studio worker authorization changed during launch')
        self._deadline(self.report)
        _send(self.report, {'ack': challenge})
        if _receive(self.report) != {'ack': challenge}:
            raise LaunchInterrupted('Studio wrapper admission was not acknowledged')
        self.admitted = True
        register(self)
        self._deadline(self.channel)
        _send(self.channel, {'ack': challenge})

    def live(self):
        return (self.wrapper_started is not None and self.started is not None
                and process_started(self.wrapper_pid) == self.wrapper_started
                and process_started(self.pid) == self.started)

    def verifies(self, pid, started, permit_id, request_id, current):
        return (self.admitted and type(pid) is int and type(started) in (int, float)
                and (pid, started, permit_id, request_id) == (
                    self.pid, self.started, self.context['permit_id'], self.context['request_id'])
                and self.live() and current() == self.context)

    def close(self):
        for channel in (self.report, self.wrapper, self.channel, self.worker):
            channel.close()


def worker_context():
    """Block the fixed worker until its actual parent launch is registered and acknowledged."""
    raw = os.environ.pop(FD_ENV, '')
    if not raw.isdecimal():
        raise LaunchInterrupted('Studio worker requires private launch admission')
    try:
        with socket.socket(fileno=int(raw)) as channel:
            expires = time.monotonic() + DEADLINE
            channel.settimeout(DEADLINE)
            offer = _receive(channel)
            if (set(offer) != {'challenge', 'job_id', 'context'}
                    or not isinstance(offer['challenge'], str) or len(offer['challenge']) != 43
                    or not isinstance(offer['job_id'], str) or not offer['job_id']):
                raise LaunchInterrupted('Invalid Studio worker launch admission')
            context = _context(offer['context'])
            _send(channel, {'challenge': offer['challenge'], 'pid': os.getpid()})
            remaining = expires - time.monotonic()
            if remaining <= 0:
                raise LaunchInterrupted('Studio launch admission expired')
            channel.settimeout(remaining)
            if _receive(channel) != {'ack': offer['challenge']}:
                raise LaunchInterrupted('Studio worker launch was not acknowledged')
            return context
    except (OSError, ValueError) as exc:
        raise LaunchInterrupted('Studio worker launch admission interrupted') from exc
