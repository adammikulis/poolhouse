"""`net.get` and `net.download` fetch any https URL the caller names; plain http reaches the built-in hosts and the
approved ones. A program is held to a list by passing the `allowed` argument of `net.Policy`, and `net.library()` is
the pipeline that lists every host."""

from __future__ import annotations

import contextlib

import pytest

from poolhouse import httpguard, net
from poolhouse.net import pipeline as pipeline_module, policy

URL = "https://api.acme.example/v1/model.gguf"
PLAIN = "http://api.acme.example/v1/model.gguf"


class Reached(Exception):
    """The request got past the host policy and would have been made."""


@pytest.fixture
def unsealed(monkeypatch):
    """No pipeline installed (the suite installs a sealed one), and a request that stops where it would be made."""
    monkeypatch.setattr(pipeline_module, "_CURRENT", None)
    monkeypatch.setattr(pipeline_module, "_STANDARD", {})
    seen: list[str] = []

    def fetch(url, *, limits, **_kw):
        limits.vet(url)
        seen.append(url)
        raise Reached(url)

    @contextlib.contextmanager
    def stream(url, *, limits, **_kw):
        limits.vet(url)
        seen.append(url)
        raise Reached(url)
        yield  # pragma: no cover

    monkeypatch.setattr(httpguard, "fetch", fetch)
    monkeypatch.setattr(httpguard, "stream", stream)
    return seen


def test_get_and_download_fetch_any_https_url_the_caller_names(unsealed, tmp_path):
    with pytest.raises(Reached):
        net.get(URL)
    with pytest.raises(Reached):
        net.download(URL, tmp_path / "model.gguf")
    assert unsealed == [URL, URL]


def test_plain_http_to_a_host_nobody_listed_is_refused_with_the_https_address_to_use(unsealed, tmp_path):
    with pytest.raises(net.NeedsApproval, match="use its https address"):
        net.get(PLAIN)
    with pytest.raises(net.NeedsApproval, match=r"api\.acme\.example"):
        net.download(PLAIN, tmp_path / "model.gguf")
    assert unsealed == [] and not (tmp_path / "model.gguf").exists()


def test_the_default_pipeline_is_the_app_one_and_the_library_one_is_a_choice(unsealed):
    assert net.default().policy.listed("huggingface.co") and not net.default().policy.listed("api.acme.example")
    assert net.default().policy.admit(URL) == "api.acme.example"
    with pytest.raises(net.NeedsApproval):
        net.default().policy.admit(PLAIN)
    assert net.library().policy.admit(PLAIN) == "api.acme.example"


def test_the_standard_policy_vouches_only_for_the_hosts_it_names(unsealed):
    policy_in_force = net.default().policy
    assert policy_in_force.vouches("https://huggingface.co/x")
    assert not policy_in_force.vouches(URL), "fetched, but a file from here is not pinned as a pull"
    assert not net.library().policy.vouches("https://huggingface.co/x")


def test_a_pipeline_argument_of_library_reaches_a_program_s_own_host(unsealed, tmp_path):
    with pytest.raises(Reached):
        net.get(URL, pipeline=net.library())
    with pytest.raises(Reached):
        net.download(URL, tmp_path / "model.gguf", pipeline=net.library())
    assert unsealed == [URL, URL]


def test_the_app_pipeline_named_as_an_argument_fetches_the_same_https_url(unsealed, tmp_path):
    with pytest.raises(Reached):
        net.get(URL, pipeline=net.default())
    with pytest.raises(Reached):
        net.download(URL, tmp_path / "model.gguf", pipeline=net.default())
    assert unsealed == [URL, URL]


def test_an_allow_list_argument_is_the_whole_policy(unsealed, tmp_path):
    only_acme = net.Pipeline(policy=net.Policy(allowed=("api.acme.example", "*.cdn.acme.example"),
                                               path=tmp_path / "approvals.jsonl"))
    with pytest.raises(Reached):
        net.get(URL, pipeline=only_acme)
    with pytest.raises(Reached):
        net.get("https://edge.cdn.acme.example/x", pipeline=only_acme)
    with pytest.raises(net.NeedsApproval, match=r"other\.example"):
        net.get("https://other.example/x", pipeline=only_acme)
    with pytest.raises(net.NeedsApproval, match=r"other\.example"):
        net.download("https://other.example/x", tmp_path / "x", pipeline=only_acme)


def test_a_refusal_by_a_named_allow_list_names_the_argument_before_the_command(unsealed, tmp_path):
    with pytest.raises(net.NeedsApproval) as raised:
        net.Policy(allowed=(), path=tmp_path / "approvals.jsonl").admit(URL, "model download")
    said = str(raised.value)
    assert said.startswith("api.acme.example is not on the allow-list for model download")
    assert 0 < said.index("net.Policy(allowed=") < said.index("poolhouse-security approve-host api.acme.example")
    assert raised.value.host == "api.acme.example"


def test_a_pipeline_the_program_installs_is_the_one_in_force_for_every_helper(unsealed, tmp_path):
    none = net.Pipeline(policy=net.Policy(allowed=(), path=tmp_path / "approvals.jsonl"))
    with net.use(none):
        assert net.default() is none and net.library() is none
        with pytest.raises(net.NeedsApproval):
            net.get(URL, pipeline=net.library())
        with pytest.raises(net.NeedsApproval):
            net.get(URL)
    assert net.library() is not none and net.default() is not none


def test_the_standard_pipelines_are_built_once_and_only_the_library_one_admits_every_host(unsealed):
    assert net.library() is net.library() and net.default() is net.default()
    assert net.library() is not net.default()
    for host in ("huggingface.co", "github.com"):
        assert net.default().policy.listed(host) and net.library().policy.listed(host)
    assert not net.default().policy.listed("api.acme.example") and net.library().policy.listed("api.acme.example")


def test_each_standard_pipeline_is_built_on_its_first_call_and_not_again(unsealed, monkeypatch):
    built: list[str] = []
    real = pipeline_module.Pipeline

    def counting(*args, **kwargs):
        built.append("built")
        return real(*args, **kwargs)

    monkeypatch.setattr(pipeline_module, "Pipeline", counting)
    for _ in range(3):
        net.default()
        net.library()
    assert built == ["built", "built"]


def test_building_the_library_pipeline_does_not_widen_the_app_one(unsealed):
    net.library()
    assert not net.default().policy.listed("api.acme.example")
    with pytest.raises(net.NeedsApproval):
        net.get(PLAIN)


def test_the_library_pipeline_keeps_the_guards_that_are_not_a_host_list(unsealed):
    lib = net.library()
    assert lib.limits == net.default().limits and not hasattr(lib, "hold")      # a failed download is deleted, never held
    assert [type(s) for s in lib.scanners] == [type(s) for s in net.default().scanners]
    assert lib.policy.allowed == policy.ANYWHERE



@pytest.mark.parametrize("allowed", [("*",), ("*.*",), ("api.acme.example", "?*"), ("*.invalid",)])
def test_an_allow_list_that_matches_any_host_lists_every_host_however_it_is_spelled(allowed, tmp_path):
    assert net.Policy(allowed=allowed, path=tmp_path / "approvals.jsonl").lists_every_host()


def test_an_allow_list_of_named_hosts_does_not_list_every_host(tmp_path):
    named = net.Policy(allowed=("api.acme.example", "*.cdn.acme.example"), path=tmp_path / "approvals.jsonl")
    assert not named.lists_every_host() and not net.default().policy.lists_every_host()
    assert not net.Policy(allowed=(), path=tmp_path / "approvals.jsonl").lists_every_host()


@pytest.mark.parametrize("hosts", ["*", "*.*", "api.acme.example,*"])
def test_a_wildcard_in_the_environment_allow_list_lists_every_host(monkeypatch, tmp_path, hosts):
    monkeypatch.setenv(policy.ENV, hosts)
    assert net.Policy(allowed=("api.acme.example",), path=tmp_path / "approvals.jsonl").lists_every_host()
    monkeypatch.setenv(policy.ENV, "edge.acme.example")
    assert not net.Policy(allowed=("api.acme.example",), path=tmp_path / "approvals.jsonl").lists_every_host()
