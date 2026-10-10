"""Steps about running tests on another device of the pool: consent, the allow-list, a run on Linux, and what a device nobody can dial does."""

from __future__ import annotations

from pool_check.device import Device, LocalDevice
from pool_check.report import Skip
from pool_check.world import World, eventually


def send(sender: Device, target: Device, *files: str):
    """`scripts/test --on` as the sender's person would run it (its own tiny tree), against ``target``'s fingerprint."""
    repo = str(sender.root_of_checkout())
    return sender.run(sender.script("send_tests.py", "--repo", repo, "--on", target.fingerprint, "--expect", target.system_name, *files), env=sender.session_env(), timeout=300, check=False)


def enable_feature(device: Device) -> None:
    """The person at ``device`` turns the experimental feature on."""
    done = device.run([*device.interpreter(), "-m", "poolhouse.features_cli", "enable", "remote-tests"], env=device.session_env(), check=False)
    assert done.returncode == 0, f"{device.name}: enabling remote-tests failed:\n{done.stdout}\n{done.stderr}"


def consent(sender: Device, receiver: Device) -> None:
    """The person at ``receiver`` names ``sender`` as a device whose tests it takes."""
    done = receiver.run([*receiver.interpreter(), "-m", "poolhouse.testfarm.consent", "on", "--from", sender.fingerprint, "--python", receiver.test_python(), "--repo", str(receiver.root_of_checkout())],
                        env=receiver.session_env(), check=False)
    assert done.returncode == 0, f"{receiver.name}: consent on failed:\n{done.stdout}\n{done.stderr}"


def a_tiny_test_file_runs_on_a_linux_device(world: World) -> None:
    """Tests do nothing on a device until it consents and names the sender; then a test file that asserts it is Linux passes there."""
    world.fresh()
    world.pool_of(world.devices)
    sender, receiver = world.first, world.others[0]
    receiver.prepare_checkout()
    enable_feature(sender)
    enable_feature(receiver)
    before = send(sender, receiver)
    assert before.returncode != 0 and "does not take tests" in before.stderr + before.stdout, f"a device nobody turned on ran tests or did not say why:\n{before.stdout}\n{before.stderr}"
    consent(sender, receiver)
    after = eventually(lambda: (lambda r: r if r.returncode == 0 else None)(send(sender, receiver)), 120, f"the tiny test file to pass on {receiver.name}")
    out = after.stdout + after.stderr
    assert "2 passed" in out and "ran on " in out, f"the run did not say it ran on the device:\n{out[-1500:]}"
    stranger = world.others[1] if world.count > 2 else None
    if stranger is not None:
        refused = send(stranger, receiver)
        assert refused.returncode != 0, f"{stranger.name}, which {receiver.name} never named, ran tests there:\n{refused.stdout}"


def a_member_nobody_can_dial_is_named_with_the_fix(world: World) -> None:
    """Asking for tests on a member the sender has no way to dial ends with the reason and what to do, not a hang or a bare error."""
    if world.count < 3:
        raise Skip("needs a member the sender never dialed (three devices)")
    world.fresh()
    world.pool_of(world.devices)
    sender, far = world.first, world.others[-1]
    assert isinstance(sender, LocalDevice)
    enable_feature(sender)
    got = send(sender, far)
    text = got.stdout + got.stderr
    assert got.returncode != 0
    assert "address" in text or "reach" in text, f"the failure does not say the member cannot be reached:\n{text[-1500:]}"
