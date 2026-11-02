"""Retained wrapper and worker process ownership for private Studio launch admission."""
from __future__ import annotations

import secrets
import socket
import time

from poolhouse.studio_launch import (
    DEADLINE,
    LaunchInterrupted,
    receive_frame,
    send_frame,
    validate_context,
)

from .job_records import process_started

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
        self.job_id, self.context = job_id, validate_context(context)
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
        reported = receive_frame(self.report)
        if set(reported) != {'pid'} or type(reported['pid']) is not int or reported['pid'] <= 0:
            raise LaunchInterrupted('Invalid Studio worker ownership report')
        self.pid, self.started = reported['pid'], process_started(reported['pid'])
        if not self.live() or wrapper.poll() is not None:
            raise LaunchInterrupted('Studio worker ownership changed during launch')
        challenge = secrets.token_urlsafe(32)
        self._deadline(self.channel)
        send_frame(self.channel, {'challenge': challenge, 'job_id': self.job_id, 'context': self.context})
        echo = receive_frame(self.channel)
        if echo != {'challenge': challenge, 'pid': self.pid}:
            raise LaunchInterrupted('Studio worker admission does not match its process')
        if not self.live() or current() != self.context:
            raise LaunchInterrupted('Studio worker authorization changed during launch')
        self._deadline(self.report)
        send_frame(self.report, {'ack': challenge})
        if receive_frame(self.report) != {'ack': challenge}:
            raise LaunchInterrupted('Studio wrapper admission was not acknowledged')
        self.admitted = True
        register(self)
        self._deadline(self.channel)
        send_frame(self.channel, {'ack': challenge})

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
