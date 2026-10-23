"""testfarm.toml: every key parses, every hostile file is refused with a sentence, and the schema is the design's table."""
from __future__ import annotations

import re
import threading
from pathlib import Path

import pytest
from testfarm_project import SHELL_CONFIG, shell_project

from poolhouse.testfarm.core import project
from poolhouse.testfarm.model import ConfigError, globs, parse, schema

DESIGN = Path(__file__).resolve().parents[1] / "docs" / "design-test-runner.md"

EVERYTHING = """
schema = 1
preset = "mine"

[presets.mine.run]
test_timeout = 5

[project]
name = "widgets"

[tree]
include = ["data/**"]
exclude = ["dist/**"]

[list]
run = ["sh", "tests/list.sh", "{step:build}"]
format = "jsonl"
starts = "{"
ends = "}"
group = "::"
allow_empty = true
timeout = 30
glob = "tests/*.sh"

[run]
argv = ["sh", "{ids}"]
ids_after = "--"
refuse_prefix = ["@", "+"]
slice = ["sh", "--shard", "{slice_index}/{slice_count}"]
slice_base = 0
slice_count = "auto"
slices_per_target = 2
slice_results = "junit"
results = "jsonl"
junit_id = "{classname}.{name}"
batch = 4
reuse = "exit"
timeout = 60
needs = ["build"]
launcher = ["env", "-i"]

[run.env]
WIDGETS_MODE = "test"

[deps]
globs = { "tests/**" = ["src/**", "tests/**"] }
command = ["sh", "tests/deps.sh"]
self = ["tests/deps.sh"]
timeout = 20
everything = ["pyproject.toml"]

[[step]]
name = "build"
run = ["sh", "build.sh", "{cache}", "{shared}", "{jobs}"]
inputs = ["src/**"]
tools = [["cc", "--version"]]
env = { CC = "cc" }
slots = 2
timeout = 300
keep = 86400
prune = ["sh", "prune.sh", "{shared}"]

[label.gpu]
match = ["tests/gpu_*"]
lease = ["gpu", "memory:8192"]
serial = "bench"
platform = ["macos"]
local = true
net = "loopback"
reuse = false
slots = 2
estimate = 30
timeout = 120
needs = ["build"]

[set.fast]
labels = ["quick"]
not_labels = ["gpu"]
ids = ["tests/**"]
extra = ["--fast"]
reuse = true

[reuse]
key_env = ["WIDGETS_MODE"]
key_tools = [["cc", "--version"]]
canary = 0.05

[failfirst]
tests = ["tests/**"]
line = "failfirst: {id} failed at {sha}: {error}"
exempt = "Exempt:"
mode = "enforce"

[schedule]
seed = "tests/durations.json"
unrecorded_max = 30
silence_margin = 3
total_margin = 4
queue_wait = 600
background_after = 900

[requires]
python = ">=3.13"
module = ["pytest"]
exe = ["cargo", "node>=20"]

[[unit]]
name = "lint"
run = ["sh", "lint.sh"]
inputs = ["src/**"]
tools = [["sh", "--version"]]
labels = ["gate"]
timeout = 60
slots = 1
env = { LINT = "1" }

[hooks]
before_plan = [["sh", "policy.sh"]]
before_run = [["sh", "before.sh"]]
after_run = [["sh", "after.sh"]]
timeout = 30
"""


def _refused(text: str, fragment: str) -> None:
    with pytest.raises(ConfigError) as caught:
        parse(text.encode())
    assert fragment in str(caught.value), str(caught.value)


def test_a_file_that_uses_every_key_of_the_schema_parses_into_values():
    config = parse(EVERYTHING.encode())
    assert config.preset == "mine" and config.run.test_timeout == 5.0 and config.run.timeout == 60.0
    assert config.list.format == "jsonl" and config.list.allow_empty and config.list.timeout == 30.0
    assert config.run.refuse_prefix == ("@", "+") and config.run.slice_base == 0 and config.run.env == (("WIDGETS_MODE", "test"),)
    assert config.deps.globs == (("tests/**", ("src/**", "tests/**")),) and config.deps.self_files == ("tests/deps.sh",)
    assert config.steps[0].prune == ("sh", "prune.sh", "{shared}") and config.steps[0].keep == 86400.0
    assert config.labels[0].lease == (("gpu", None), ("memory", 8192)) and config.labels[0].net == "loopback"
    assert config.sets[0].not_labels == ("gpu",) and config.reuse.canary == 0.05 and config.failfirst.mode == "enforce"
    assert config.schedule.silence_margin == 3.0 and config.requires.exe == ("cargo", "node>=20")
    assert config.units[0].name == "lint" and config.hooks.before_plan == (("sh", "policy.sh"),)
    assert re.fullmatch(r"[0-9a-f]{64}", config.sha256)


def design_keys() -> set[str]:
    """The ``table.key`` names the first column of the design's key table (section 4.1) names."""
    text = DESIGN.read_text(encoding="utf-8")
    block = text[text.index("### 4.1 Reference"):text.index("The file has no key that chooses a target")]
    found: set[str] = set()
    for row in re.findall(r"^\| ([^|]+) \|", block, re.M)[2:]:
        table = ""
        for item in re.findall(r"`([^`]+)`", row):
            header = re.match(r"\[\[?([a-z]+)(?:\.NAME)?\]\]?\s*(.*)", item)
            if header:
                table, item = header.group(1), header.group(2)
            found |= {f"{table}.{key.strip()}" for key in item.split(",") if key.strip()}
    return found


def missing_from(keys: set[str], known: set[str]) -> set[str]:
    """The keys of ``keys`` the schema ``known`` does not hold."""
    return {key for key in keys if key.startswith(".") is False and key not in known and key not in ("schema", "preset", "presets")}


def test_the_schema_has_every_key_of_the_design_table():
    wanted = {key.removeprefix(".") for key in design_keys()}
    assert len(wanted) > 60, "the key table was not read"
    assert missing_from(wanted, set(schema.KEYS)) == set()
    assert missing_from(wanted, set(schema.KEYS) - {"list.allow_empty"}) == {"list.allow_empty"}


def test_a_remote_or_target_key_in_the_project_file_is_an_unknown_key():
    body = '[run]\nargv = ["sh", "{ids}"]\ntimeout = 5\n'
    for key in ("target", "remote", "on", "device", "pool"):
        _refused(f'schema = 1\n{key} = "pool"\n' + body, f"{key!r} is not a key")
        for table in ("run", "project", "tree", "schedule", "reuse", "hooks"):
            extra = {"run": body + f'{key} = "pool"\n'}.get(table, body + f'[{table}]\n{key} = "pool"\n')
            _refused("schema = 1\n" + extra, f"{key!r} is not a key")


def test_a_run_with_no_hang_guard_is_refused():
    base = 'schema = 1\n[list]\nrun = ["sh", "list.sh"]\n[run]\nargv = ["sh", "{ids}"]\n'
    _refused(base, "a run with no hang guard is not allowed")
    _refused(base + "timeout = 5\n", "LIST has no timeout")
    _refused(base.replace('"list.sh"]', '"list.sh"]\ntimeout = 5') + "[schedule]\nsilence_margin = 3\n", "a run with no hang guard")
    assert parse((base + "timeout = 5\n[schedule]\nsilence_margin = 3\ntotal_margin = 4\n").encode()).schedule.total_margin == 4.0
    assert parse(base.replace('"list.sh"]', '"list.sh"]\ntimeout = 5').encode() + b"timeout = 5\n").run.timeout == 5.0
    unguarded = [("[[step]]\nname = 'build'\nrun = ['sh']\n", "step build"), ("[[unit]]\nname = 'lint'\nrun = ['sh']\n", "unit lint"),
                 ("[hooks]\nbefore_plan = [['sh']]\n", "the hooks"), ("[deps]\ncommand = ['sh']\n", "the deps command")]
    for table, who in unguarded:
        _refused('schema = 1\n[run]\nargv = ["sh", "x"]\ntimeout = 5\n' + table, who)


def test_list_has_allow_empty_and_a_timeout_key():
    config = parse(b'schema = 1\n[list]\nrun = ["sh"]\nallow_empty = true\ntimeout = 12\n[run]\nargv = ["sh", "{ids}"]\ntimeout = 5\n')
    assert config.list.allow_empty is True and config.list.timeout == 12.0
    _refused('schema = 1\n[list]\nrun = ["sh"]\nallow_empty = "yes"\ntimeout = 12\n[run]\nargv = ["sh", "{ids}"]\ntimeout = 5\n', "true or false")


def test_names_and_environment_variables_outside_their_grammar_are_refused():
    head = 'schema = 1\n[run]\nargv = ["sh", "{ids}"]\ntimeout = 5\n'
    for table, fragment in [("[set.Bad]\nlabels = []", "not a name"), ("[label.a_b.c]\nlocal = true", "not a key"),
                            ("[label.'a b']\nlocal = true", "not a name"), ("[label.'a/b']\nlocal = true", "not a name"),
                            ("[[step]]\nname = 'UPPER'\nrun = ['sh']\ntimeout = 5", "not a name"),
                            ("[[unit]]\nname = '../x'\nrun = ['sh']\ntimeout = 5", "not a name"),
                            ('[run.env]\n"A=B" = "x"', "not an environment variable name"),
                            ('[run.env]\n"1A" = "x"', "not an environment variable name"),
                            ('[run.env]\nA = "x\\u0000y"', "NUL"),
                            ("[reuse]\nkey_env = ['A B']\ncanary = 0.5", "not an environment variable name"),
                            ("[[step]]\nname = 's'\nrun = ['sh', '{step:../x}']\ntimeout = 5", "is not a placeholder"),
                            ("[[step]]\nname = 's'\nrun = ['sh', '{shared:a b}']\ntimeout = 5", "is not a placeholder"),
                            ("[[step]]\nname = 's'\nrun = ['sh', '{step:other}']\ntimeout = 5", "names no step"),
                            ("[label.x]\nneeds = ['nothing']", "names no step"),
                            ("[requires]\nexe = ['-evil']", "not a program name"), ("[requires]\nmodule = ['.hidden']", "does not begin with"),
                            ("[label.x]\nlease = ['gpu:']", "not a resource name"), ("[label.x]\nlease = ['GPU']", "not a resource name")]:
        _refused(head + table + "\n", fragment)


def test_placeholders_are_only_the_listed_ones_in_the_places_they_belong():
    for argv, fragment in [('["sh", "{nope}"]', "is not a placeholder"), ('["{ids}"]', "the program name holds no placeholder"),
                           ('["sh", "x{ids}"]', "whole argument"), ('["sh", "a {"]', "not a placeholder"),
                           ('["sh", "{slice_index}"]', "is not a placeholder"), ('"sh {ids}"', "shell string")]:
        _refused(f"schema = 1\n[run]\ntimeout = 5\nargv = {argv}\n", fragment)
    assert parse(b'schema = 1\n[run]\ntimeout = 5\nargv = ["sh", "-c", "echo {{x}}", "{ids}"]\n').run.argv[2] == "echo {{x}}"
    _refused('schema = 1\n[run]\ntimeout = 5\nslice = ["sh", "{slice_index}", "{slice_index}", "{slice_count}"]\n', "exactly one {slice_index}")
    _refused('schema = 1\n[run]\ntimeout = 5\nslice = ["sh", "{slice_index}"]\n', "exactly one {slice_count}")
    _refused('schema = 1\n[list]\nrun = ["sh", "{ids}"]\ntimeout = 5\n[run]\nargv = ["sh", "x"]\ntimeout = 5\n', "is not a placeholder")
    _refused('schema = 1\n[list]\nrun = ["sh"]\ntimeout = 5\n[run]\nargv = ["sh", "x"]\ntimeout = 5\n', "must hold {ids} or {idfile}")


def test_paths_that_leave_the_project_are_refused():
    head = 'schema = 1\n[run]\nargv = ["sh", "{ids}"]\ntimeout = 5\n'
    for glob in ('"../x"', '"a/../../x"', '"/etc/passwd"', '"C:/x"', "'\\\\host\\share'", '"a\\u0000b"'):
        _refused(head + f"[tree]\ninclude = [{glob}]\n", "glob")
    _refused(head + '[schedule]\nseed = "../durations.json"\n', "stays inside")


def test_nan_infinity_negative_and_oversized_seconds_are_refused():
    head = 'schema = 1\n[run]\nargv = ["sh", "{ids}"]\n'
    for value in ("nan", "+nan", "inf", "-inf", "0", "-3", "1e999", "1e12", "true", '"5"', "[5]"):
        _refused(head + f"timeout = {value}\n", "timeout")
    for table, key in [("[schedule]", "silence_margin"), ("[schedule]", "total_margin"), ("[schedule]", "queue_wait"),
                       ("[schedule]", "unrecorded_max"), ("[schedule]", "background_after"), ("[reuse]", "canary"),
                       ("[label.x]", "estimate"), ("[label.x]", "timeout"), ("[label.x]", "slots")]:
        for value in ("nan", "inf", "-1", "0"):
            _refused(head + f"timeout = 5\n{table}\n{key} = {value}\n", key)
    for key in ("test_timeout", "batch", "slices_per_target"):
        for value in ("nan", "inf", "-1", "0"):
            _refused(head + f"timeout = 5\n{key} = {value}\n", key)
    _refused(head + "timeout = 5\n[schedule]\nsilence_margin = 0.5\n", "at least 1")
    _refused(head + "timeout = 5\n[reuse]\ncanary = 1.5\n", "at most 1")
    _refused(head + "timeout = 5\nslice_base = 2\n", "slice_base")
    assert parse((head + f"timeout = {threading.TIMEOUT_MAX}\n").encode()).run.timeout == threading.TIMEOUT_MAX


def test_toml_nesting_that_raises_recursionerror_is_a_validation_error():
    for text in ("a = " + "[" * 100000 + "]" * 100000, "a = " + "{b=" * 5000 + "1" + "}" * 5000, "[a]\n" + "x = " + "[" * 5000 + "]" * 5000):
        _refused("schema = 1\n" + text, "nested too deeply")


def test_no_key_takes_a_regular_expression():
    assert not [key for key in schema.KEYS if re.search(r"regex|regexp|pattern", key)]
    config = parse(b'schema = 1\n[list]\nrun = ["sh"]\ntimeout = 5\nstarts = "^t.*$"\n[run]\nargv = ["sh", "{ids}"]\ntimeout = 5\n'
                   b'[label.slow]\nmatch = ["(a+)+$", "[a-z]+", "t_{1,2}"]\n')
    assert config.list.starts == "^t.*$" and config.labels[0].match == ("(a+)+$", "[a-z]+", "t_{1,2}")
    assert globs.match("(a+)+$", "(a+)+$") and not globs.match("(a+)+$", "aaa")
    assert globs.match("[a-z]+", "[a-z]+") and not globs.match("[a-z]+", "abc")
    assert not globs.match("*a*a*a*a*a*a*a*a*a*a*a*a*a*b", "a" * 5000)


@pytest.mark.parametrize("data, fragment", [
    (b"", "is required: write schema = 1"), (b"schema = 2", "must be 1"), (b'schema = "1"', "must be 1"), (b"schema = true", "must be 1"),
    (b"schema = 1\n", "describes nothing"), (b"schema = 1\n[", "not valid TOML"), (b"\xff\xfe\x00", "not UTF-8"),
    (b'schema = 1\n[run]\nargvv = ["sh"]', "did you mean 'argv'"), (b"schema = 1\n[runn]\nargv = []", "did you mean 'run'"),
    (b'schema = 1\n[run]\nargv = []\ntimeout = 5', "at least the program"), (b'schema = 1\n[run]\nargv = [""]\ntimeout = 5', "non-empty text"),
    (b'schema = 1\n[run]\nargv = ["a\\u0000"]\ntimeout = 5', "NUL"), (b'schema = 1\n[run]\nargv = [1]\ntimeout = 5', "non-empty text"),
    (b'schema = 1\nrun = 5', "must be a table"), (b'schema = 1\n[list]\ntimeout = 5', "needs run"),
    (b'schema = 1\n[run]\ntimeout = 5', "needs argv"), (b'schema = 1\n[list]\nrun = ["sh"]\nformat = "regex"\ntimeout = 5', "one of lines, jsonl"),
    (b'schema = 1\n[run]\nargv = ["sh", "{ids}"]\ntimeout = 5\nresults = "xml"', "one of exit, jsonl, junit"),
    (b'schema = 1\n[run]\nargv = ["sh", "{ids}"]\ntimeout = 5\nreuse = "yes"', "one of exit"),
    (b'schema = 1\n[run]\nargv = ["sh", "{ids}"]\ntimeout = 5\njunit_id = "{classname}{nope}"', "classname, name"),
    (b'schema = 1\n[[step]]\nname = "a"\nrun = ["sh"]\ntimeout = 5\n[[step]]\nname = "a"\nrun = ["sh"]\ntimeout = 5\n[run]\nargv = ["x"]\ntimeout=5',
     "named twice"),
    (b'schema = 1\npreset = "nowhere"\n[run]\nargv = ["x"]\ntimeout = 5', "is not a preset"),
    (b'schema = 1\nunit = 5', "[[unit]]"), (b'schema = 1\n[[step]]\nrun = ["sh"]\ntimeout = 5\n[run]\nargv = ["x"]\ntimeout = 5', "needs name and run"),
    (b'schema = 1\n[label]\nx = 5', "must be a table"), (b'schema = 1\n[label.x]\nmatch = "tests/*"\n[run]\nargv = ["x"]\ntimeout=5', "must be an array"),
    (b'schema = 1\n[deps]\nglobs = { "tests/**" = "src/**" }\n[run]\nargv = ["x"]\ntimeout=5', "must be an array"),
    (b'schema = 1\n[requires]\npython = "3.13 or later"\n[run]\nargv = ["x"]\ntimeout=5', "version bound"),
])
def test_every_hostile_file_is_refused_with_a_sentence(data, fragment):
    with pytest.raises(ConfigError) as caught:
        parse(data)
    sentence = str(caught.value)
    assert fragment in sentence, sentence
    assert sentence.startswith(("testfarm.toml:", "no testfarm")) and "\n" not in sentence and "Traceback" not in sentence


def test_a_preset_the_project_defines_is_the_base_of_the_file_and_cannot_widen_the_sandbox():
    config = parse(b'schema = 1\npreset = "mine"\n[presets.mine.run]\nargv = ["sh", "{ids}"]\ntimeout = 5\n'
                   b'[presets.mine.list]\nrun = ["sh", "l"]\ntimeout = 3\n[presets.mine.confine]\nread = ["/"]\n[run]\ntimeout = 9\n')
    assert config.run.argv == ("sh", "{ids}") and config.run.timeout == 9.0 and config.list.timeout == 3.0
    assert config.notes and "cannot widen its own sandbox" in config.notes[0]


def test_check_fails_a_deps_glob_that_matches_no_file_and_a_set_without_the_units_own_file(tmp_path):
    point = SHELL_CONFIG + '\n[deps]\nglobs = { "tests/*" = ["lib/**"] }\n'
    root = shell_project(tmp_path / "empty", point)
    with pytest.raises(ConfigError, match=r"matches no file"):
        project.check(root, tmp_path)
    (root / "lib").mkdir()
    (root / "lib" / "core.sh").write_text("exit 0\n")
    with pytest.raises(ConfigError, match=r"leave out tests/t_parse.sh itself"):
        project.check(root, tmp_path)
    (root / "testfarm.toml").write_text(SHELL_CONFIG + '\n[deps]\nglobs = { "tests/*" = ["lib/**", "tests/*"] }\n')
    assert any(line.startswith("deps: 2 of 2 units declare their files") for line in project.check(root, tmp_path))


def test_a_step_input_and_a_unit_input_that_match_no_file_are_check_errors(tmp_path):
    for table in ("[[step]]\nname = 'build'\nrun = ['sh']\ntimeout = 5\ninputs = ['src/**']\n",
                  "[[unit]]\nname = 'lint'\nrun = ['sh']\ntimeout = 5\ninputs = ['src/**']\n"):
        root = shell_project(tmp_path / table[:6].strip("[]\n"), SHELL_CONFIG + table)
        with pytest.raises(ConfigError, match=r"'src/\*\*' matches no file"):
            project.check(root, tmp_path)
