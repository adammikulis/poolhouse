"""Actual wrapper/child launch admission never derives authority from worker metadata."""
import copy
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from poolhouse.fleet import job_exit
from poolhouse.fleet.job_records import process_started
from poolhouse.fleet.jobs import JobRunner
from poolhouse.fleet.studio_worker_launch import LaunchProof
from poolhouse.studio_launch import (
    FD_ENV,
    LaunchInterrupted,
    receive_frame,
    send_frame,
    worker_context,
)

SRC = str(Path(__file__).resolve().parents[1] / 'src')


def context():
    return {'permit_id': 'permit', 'request_id': 'request', 'run_id': 'run', 'node_id': 'node',
            'request_digest': 'a' * 64, 'model_digest': 'b' * 64, 'calibration_digest': 'c' * 64,
            'broker': {'pid': os.getpid(), 'started': process_started(os.getpid()),
                       'instance': 'broker-instance', 'port': 12345}}


def wait_for(check):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        result = check()
        if result:
            return result
        time.sleep(.01)
    pytest.fail('Private Studio launch did not settle')


def child_command(output, release):
    return [sys.executable, '-c',
            f'import sys;sys.path.insert(0,{SRC!r});'
            'from poolhouse.studio_launch import worker_context;'
            'import json,os,time;'
            'context=worker_context();'
            f'open({str(output)!r},"w").write(json.dumps({{"pid":os.getpid(),"context":context}}));'
            f'\nwhile not os.path.exists({str(release)!r}): time.sleep(.01)']


def test_real_owned_worker_receives_protected_context_and_live_proof(tmp_path):
    runner = JobRunner(tmp_path / 'runner')
    expected = context()
    current = {'value': expected}
    runner.studio_launch_context = lambda _job: copy.deepcopy(current['value'])
    output, release = tmp_path / 'worker.json', tmp_path / 'release'
    job = runner.submit('decision', child_command(output, release), str(tmp_path))
    try:
        wait_for(output.exists)
        actual = json.loads(output.read_text())
        pid, started = actual['pid'], process_started(actual['pid'])
        assert pid != job.pid and actual['context'] == expected
        assert runner.verify_studio_worker(job.id, pid, started, 'permit', 'request') is True
        assert runner.verify_studio_worker(job.id, job.pid, job.process_started, 'permit', 'request') is False
        for changes in [(pid + 1, started, 'permit', 'request'), (pid, started + 1, 'permit', 'request'),
                        (pid, started, 'other', 'request'), (pid, started, 'permit', 'other')]:
            assert runner.verify_studio_worker(job.id, *changes) is False
        current['value'] = {**expected, 'model_digest': 'c' * 64}
        assert runner.verify_studio_worker(job.id, pid, started, 'permit', 'request') is False
        current['value'] = {**expected, 'calibration_digest': 'd' * 64}
        assert runner.verify_studio_worker(job.id, pid, started, 'permit', 'request') is False
        current['value'] = expected
        public = json.dumps(runner.snapshot())
        stored = (runner.job_dir(job.id) / 'job.json').read_text()
        assert 'broker-instance' not in public + stored and 'model_digest' not in public + stored
        release.touch()
        wait_for(lambda: job.state == 'done')
        assert runner.verify_studio_worker(job.id, pid, started, 'permit', 'request') is False
    finally:
        release.touch()
        runner.stop_running(grace_s=1)
        runner.shutdown()


def test_revocation_during_private_handshake_interrupts_without_worker_effect(tmp_path):
    runner = JobRunner(tmp_path / 'runner')
    calls = 0
    def current(_job):
        nonlocal calls
        calls += 1
        return context() if calls == 1 else None
    runner.studio_launch_context = current
    output, release = tmp_path / 'effect', tmp_path / 'release'
    job = runner.submit('decision', child_command(output, release), str(tmp_path))
    try:
        wait_for(lambda: job.state == 'interrupted')
        assert not output.exists()
        assert runner.status()['free'] == 1
        assert not runner._stop.is_set()
    finally:
        runner.stop_running(grace_s=1)
        runner.shutdown()


def test_locator_or_public_job_environment_cannot_mint_admission(tmp_path, monkeypatch):
    monkeypatch.setenv(FD_ENV, '987654')
    with pytest.raises(LaunchInterrupted):
        worker_context()
    runner = JobRunner(tmp_path / 'runner')
    output, release = tmp_path / 'effect', tmp_path / 'release'
    job = runner.submit('generic', child_command(output, release), str(tmp_path), env={FD_ENV: '1'})
    try:
        wait_for(lambda: job.state == 'failed')
        assert not output.exists() and runner._studio_workers == {}
    finally:
        runner.shutdown()


@pytest.mark.parametrize('spoof', ['wrong_pid', 'replay', 'lost_ack', 'dead_parent'])
def test_fake_worker_or_lost_private_ack_never_produces_admission(tmp_path, spoof):
    proof = LaunchProof('job', context())
    child = None
    script = ('import os,socket,json,struct,time;'
              f's=socket.socket(fileno=int(os.environ[{FD_ENV!r}]));'
              'n=int.from_bytes(s.recv(4),"big");offer=json.loads(s.recv(n));'
              f'pid=os.getpid()+{1 if spoof == "wrong_pid" else 0};'
              f'challenge={"\"old-challenge\"" if spoof == "replay" else "offer[\"challenge\"]"};'
              'body=json.dumps({"challenge":challenge,"pid":pid}).encode();'
              's.sendall(len(body).to_bytes(4,"big")+body);'
              + ('s.close()' if spoof == 'lost_ack' else 'time.sleep(.2)'))
    try:
        with (tmp_path / 'log').open('wb') as log:
            child = subprocess.Popen(job_exit.command(tmp_path, [sys.executable, '-c', script], setup=proof.setup()),
                                     pass_fds=proof.descriptors, stdout=log, stderr=log)
            if spoof == 'dead_parent':
                child.kill()
                child.wait()
            with pytest.raises((OSError, ValueError)):
                proof.admit(child, lambda: context(), lambda _: None)
            assert proof.admitted is False or spoof == 'lost_ack'
    finally:
        proof.close()
        if child is not None:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)


def test_worker_refuses_offer_without_final_ack(monkeypatch):
    worker, server = socket.socketpair()
    monkeypatch.setenv(FD_ENV, str(worker.detach()))
    def offer():
        with server:
            send_frame(server, {'challenge': 'c' * 43, 'job_id': 'job', 'context': context()})
            assert receive_frame(server) == {'challenge': 'c' * 43, 'pid': os.getpid()}
    thread = threading.Thread(target=offer)
    thread.start()
    try:
        with pytest.raises(LaunchInterrupted):
            worker_context()
    finally:
        thread.join(timeout=5)
    assert not thread.is_alive()


@pytest.mark.parametrize('partial', [False, True])
def test_wrapper_deadline_and_ignoring_term_child_cleanup_are_bounded(tmp_path, monkeypatch, partial):
    from poolhouse.fleet import studio_worker_launch
    monkeypatch.setattr(studio_worker_launch, 'DEADLINE', .3)
    proof = LaunchProof('job', context())
    ready = tmp_path / 'ready'
    program = ('import signal,time,pathlib;signal.signal(signal.SIGTERM,signal.SIG_IGN);'
               f'pathlib.Path({str(ready)!r}).touch();time.sleep(60)')
    process = None
    try:
        process = subprocess.Popen(job_exit.command(tmp_path, [sys.executable, '-c', program], setup=proof.setup()),
                                   pass_fds=proof.descriptors, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proof.wrapper.close()
        proof.worker.close()
        child_pid = receive_frame(proof.report)['pid']
        wait_for(ready.exists)
        if partial:
            proof.report.sendall(b'\x00\x00')  # Open channel stalls in the middle of a frame.
        else:
            proof.report.shutdown(socket.SHUT_WR)
        assert process.wait(timeout=3) != 0
        wait_for(lambda: process_started(child_pid) is None)
    finally:
        proof.close()
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=3)



@pytest.mark.parametrize('calibration', [None, '', 'g' * 64, 'c' * 63, 17])
def test_calibration_identity_is_required_in_private_context(calibration):
    value = context()
    if calibration is None:
        value.pop('calibration_digest')
    else:
        value['calibration_digest'] = calibration
    with pytest.raises(LaunchInterrupted):
        LaunchProof('job', value)
