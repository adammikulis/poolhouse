"""The shell rules read a command line twice, by the line and as the shell does, and refuse what either reading shows: no comment,
quote, expansion, heredoc or operator hides a command, nothing refused before is allowed, and no input stops the guards from
answering. Every command here is only judged, never run."""

from __future__ import annotations
import __future__

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from poolhouse import protectedwrites
from poolhouse.guard import unboxed

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "scripts" / "hooks"
GUARD = HOOKS / "claude-bash-guard"
KEY = "~/.poolhouse/person/key"
TOKEN = "~/.poolhouse/node/client/p/lead.token"
REGISTRY = "~/.poolhouse/authority.json"
HOOK = ".git/hooks/pre-push"
ABSOLUTE = "/Users/x/.poolhouse/person/key"
UNSEEN = "ls # <<NEVER\n"
"""A comment the line-by-line reading takes for a heredoc, so that it passes over everything after: what follows is refused only
if the shell's reading shows it."""


@pytest.fixture
def tree(tmp_path) -> Path:
    (tmp_path / "home").mkdir()
    (tmp_path / "work" / ".git" / "hooks").mkdir(parents=True)
    return tmp_path / "work"


def why(command: str, tree: Path) -> str:
    return protectedwrites.refusal(command, str(tree), lambda _: "no accept", {"HOME": str(tree.parent / "home")})


def hook(command: str, tree: Path, **event) -> subprocess.CompletedProcess:
    sent = {"tool_name": "Bash", "cwd": str(tree), "tool_input": {"command": command}, **event}
    env = {**os.environ, "HOME": str(tree.parent / "home"), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, str(GUARD)], input=json.dumps(sent), text=True, capture_output=True, env=env, timeout=90)


HIDDEN_BY_A_COMMENT = {
    "after a comment line": f"echo hi # note\ncat {KEY}",
    "a hook removed after a comment": f"ls # note\nrm {HOOK}",
    "heredoc opener in a comment": f"ls # <<X\ncat {KEY}",
    "heredoc opener in quotes": f"ls '<<X'\ncat {KEY}",
    "heredoc opener in quotes, a write": f"ls '<<X'\nrm {HOOK}",
    "heredoc opener in arithmetic": f"ls $((1<<x))\ncat {KEY}",
    "comment after a quote over two lines": f"ls 'a\nb' ; ls ' ' # <<X\ncat {KEY}",
    "comment after a closed open quote": f"ls 'a\n' # <<X\ncat {KEY}",
    "hash in a parameter default": f"echo ${{x:- #}}; cat {KEY}",
    "hash in a parameter default, a write": f"echo ${{x:- #}}; rm {HOOK}",
    "hash after an escaped blank": f"echo \\ #; cat {KEY}",
    "hash after an escaped blank, a write": f"echo \\ #; rm {HOOK}",
    "quotes nested in a substitution": f"echo \"$(echo \"a #\")\"; cat {KEY}",
    "nested quotes, a hook": f"echo \"$(echo \"a #\")\"; rm {HOOK}",
    "nested quotes, the registry": f"echo \"$(echo \"a #\")\"; echo x > {REGISTRY}",
    "nested quotes, hooks path": "echo \"$(date \"+ #%s\")\"; git config core.hooksPath /tmp/x",
    "nested quotes, admin merge": "echo \"$(date \"+ #%s\")\"; gh pr merge 3 --admin",
    "nested quotes in a count": f"echo \"count: $(grep -c \"^ #\" setup.cfg)\"; cat {KEY}",
    "quote after the process id": f"echo $$'\\'\nrm {HOOK}",
    "quote after the process id, config": "echo $$'\\'\ngit config core.hooksPath /tmp/x",
    "escaped quote in a dollar quote": f"echo $'it\\'s'\nrm {HOOK}",
    "hash inside a word": f"echo a#b; cat {KEY}",
    "a substitution in a heredoc": f"cat <<EOF\n$(cat {KEY})\nEOF",
    "after a heredoc": f"cat <<EOF\ntext\nEOF\ncat {KEY}",
}

GLUED_OPERATORS = {
    "and then a new line": f"echo ok &&\ncat {KEY}",
    "or then a new line": f"echo ok ||\ncat {KEY}",
    "three new lines": f"echo start\n\n\ncat {KEY}",
    "comment lines between": f"echo start\n# one\n# two\ncat {KEY}",
    "a subshell after a semicolon": f"echo hi;(cat {KEY})",
    "a subshell on the next line": f"echo hi\n(cat {KEY})",
    "after closing subshells": f"(echo hi)\necho done; (echo a)\ncat {KEY}",
    "a token after and": f"echo ok &&\ncat {TOKEN}",
    "a hook after and": f"echo ok &&\nrm {HOOK}",
    "a hook after three lines": f"echo ok\n\n\nrm {HOOK}",
    "a case arm": f"case x in a) true;;\nesac\nrm {HOOK}",
    "background then a new line": f"sleep 1 &\nrm {HOOK}",
}

REDIRECT_SPELLINGS = {
    "to a hook": f"echo x >& {HOOK}",
    "to the registry": f"echo x >& {REGISTRY}",
    "in shell code": f"bash -c 'echo x >& {HOOK}'",
    "read and write": f"cat <> {HOOK}",
    **{f"zsh {operator}": f"echo x {operator} {HOOK}" for operator in (">>&", ">!", ">>|", "&>|", ">&|", "&>!", ">>!")},
    "a named descriptor": f"exec {{fd}}> {HOOK}",
    "a second target": f"echo x > out > {HOOK}",
}

COMPOUND_COMMANDS = {
    "if": f"bash -c 'if true; then rm {HOOK}; fi'",
    "else": f"bash -c 'if false; then true; else rm {HOOK}; fi'",
    "braces": f"bash -c '{{ rm {HOOK}; }}'",
    "loop": f"bash -c 'for f in a; do rm {HOOK}; done'",
    "while": f"while true; do rm {HOOK}; done",
    "negation": f"! rm {HOOK}",
    "top level if": f"if true\nthen\n  rm {HOOK}\nfi",
    "function": f"f() {{ rm {HOOK}; }}; f",
    "function keyword": f"function f {{ rm {HOOK}; }}; f",
    "time": f"time {{ rm {HOOK}; }}",
    "noglob": f"noglob rm {HOOK}",
    "nocorrect": f"nocorrect rm {HOOK}",
    "a dash": f"- rm {HOOK}",
    "repeat": f"repeat 1 rm {HOOK}",
    "a program's path": f"=rm {HOOK}",
    "short if": f"if [[ 1 == 1 ]] rm {HOOK}",
    "short for": f"for x (a) rm {HOOK}",
    "always": f"{{ true }} always {{ rm {HOOK} }}",
    "braces with no semicolon": f"{{ rm {HOOK} }}",
    "select": f"select x in a; do rm {HOOK}; break; done <<< 1",
    "redirect first": f">/dev/null rm {HOOK}",
}

INTERPRETER_NAMES = {
    "python3.12 reads": f"python3.12 -c 'print(open(\"{ABSOLUTE}\").read())'",
    "python3.12 writes": f"python3.12 -c 'open(\"{HOOK}\",\"w\")'",
    "a virtual environment's": ".venv/bin/python3.11 -c 'import shutil; shutil.rmtree(\"/Users/x/.poolhouse/person\")'",
    "pythonw": f"pythonw -c 'open(\"{ABSOLUTE}\")'",
    "free threaded": f"python3.14t -c 'open(\"{ABSOLUTE}\")'",
    "by full path": "/opt/homebrew/bin/python3.12 -c 'open(\"/Users/x/.poolhouse/authority.json\",\"w\")'",
    "a heredoc": f"python3.12 - <<'EOF'\nopen('{ABSOLUTE}')\nEOF",
    "pypy": f"pypy3 -c 'open(\"{ABSOLUTE}\")'",
    "perl by version": f"perl5.34 -e 'open F, \"<{ABSOLUTE}\"'",
    "ruby by version": f"ruby3.2 -e 'File.read(\"{ABSOLUTE}\")'",
    "editing in place": f"perl5.34 -pi -e 's/a/b/' {HOOK}",
}

HANDED_TO_A_PROGRAM = {
    "a here-string": f"read p <<< {KEY}; cat \"$p\"",
    "another program's -m": f"sort -m {KEY}",
    "wc -m": f"wc -m {KEY}",
    "a file beside a commit message": f"git commit -m '{KEY}' --file {KEY}",
}

SHAPES = {
    "control": "@@",
    "quote in a quoted default": "echo \"${x:-'}\"; @@",
    "apostrophes in quoted defaults": "echo \"${x:-don't}\"\n@@\necho \"${y:-can't}\"",
    "ansi quote in a default": "echo ${x:-$'\\''}; @@",
    "ansi quote closed later": "echo ${x:-$'\\''}; @@ #'}",
    "process substitution in a default": "cat ${x:-<(@@)}",
    "output substitution in a default": "echo hi > ${x:->(@@)}",
    "zsh substitution in a default": "cat ${x:-=(@@)}",
    "assigning default": ": ${x:=<(@@)}",
    "zsh flag with a quote": "echo ${(j:':)x}; @@",
    "zsh flag with a brace": "echo ${(j:}:)x:-'}; @@ #'}",
    "carriage return before a hash": "echo a\r#; @@",
    "carriage return and blank": "true x\r# ; @@",
    "zsh test flag": "[[ a == (#i)A ]]; @@",
    "zsh test hash": "[[ a == (#) ]]; @@",
    "zsh test alternative": "[[ a == (#|a) ]] && @@",
    "glob hash after a parenthesis": "noglob echo a(b)#; @@",
    "assignment hash": "x=a(#); @@",
    "array then hash": "x=(a)#; @@",
    "case pattern hash": "case x in (#|x) @@;; esac",
    "arithmetic backticks": "echo $((`@@`+1))",
    "arithmetic assignment backticks": "((x=`@@`))",
    "quoted close in arithmetic": "((echo \"))\"; @@) )",
    "quoted close in dollar arithmetic": "echo $((echo \"))\"; @@) )",
    "single quoted close": "((echo '))'; @@) )",
    "single quoted close assigned": "x=$((echo '))' && @@) )",
    "quoted comment in arithmetic": "((echo \")) #\"\n@@) )",
    "comment in arithmetic": "((@@ #))\n) )",
    "comment in dollar arithmetic": "echo $((@@ #))\n) )",
    "command before a quoted close": "((@@; echo \"))\" ) )",
    "bracket arithmetic": "echo $[1<<2]\n@@",
    "bracket arithmetic assigned": "x=$[ 1 << 4 ]\n@@",
    "bracket arithmetic with a name": "echo $[1<<x]\n@@\nx",
    "subscript": "a[1<<2]=5\n@@",
    "subscript with a name": "a[1<<x]=5\n@@\nx",
    "zsh dollar subscript": "echo $a[1<<x]\n@@\nx",
    "arithmetic if": "if((1<<x));then :;fi\n@@\nx",
    "arithmetic for": "for((i=1<<x;i<1;i++));do :;done\n@@\nx",
    "arithmetic while": "while((0<<x));do break;done\n@@\nx",
    "long arithmetic": "(( " + "0+" * 2100 + "1<<x ))\n@@\nx",
    "arithmetic in a test": "[[ 1 -lt 1<<x ]]\n@@\nx",
    "arithmetic subscript": "((a[0]=1<<x))\n@@\nx",
    "ansi delimiter": "cat <<$'EOF'\nx\nEOF\n@@",
    "locale delimiter": "cat <<$\"EOF\"\nx\nEOF\n@@",
    "substitution delimiter": "cat <<$(a)\nx\n$(a)\n@@",
    "expansion delimiter": "cat <<E${a}\nx\nE${a}\n@@",
    "parenthesis after a delimiter": "cat <<E(1)\nx\nE\n@@",
    "continued delimiter": "cat <<E\\\nOF\nx\nEOF\n@@",
    "continued before the delimiter": "cat <<\\\nEOF\n'\nEOF\n@@",
    "continued operator": "cat <\\\n<EOF\n'\nEOF\n@@",
    "empty delimiter": "cat <<''\n'\n\n@@",
    "spaced closing line": "cat <<EOF\n EOF\n'\nEOF\n@@",
    "spaced line then another opener": "cat <<EOF\n EOF\ncat <<ZZ\nEOF\n@@",
    "trailing space on a line": "cat <<EOF\nEOF \n'\nEOF\n@@",
    "dash with spaces": "cat <<-EOF\n  EOF\n'\n\tEOF\n@@",
    "joined closing line": "cat <<EOF\nEO\\\nF\n@@",
    "joined body line": "cat <<EOF\na\\\nEOF\n'\nEOF\n@@",
    "close inside a substitution": "x=$(cat <<EOF\nfoo\nEOF)\n@@",
    "substitution closed on its line": "echo $(cat <<EOF)\n'\nEOF\n@@",
    "quoted substitution closed on its line": "echo \"$(cat <<EOF)\"\n'\nEOF\n@@",
    "process substitution closed on its line": "cat <(cat <<EOF)\n'\nEOF\n@@",
    "backticks closed on their line": "echo `cat <<EOF`\n'\nEOF\n@@",
    "subshell closed on its line": "(cat <<EOF)\n'\nEOF\n@@",
    "windows line ends": "cat <<EOF\r\nx\r\nEOF\r\n@@",
    "carriage return in the opener only": "cat <<EOF\r\nx\nEOF\r\n@@",
    "number delimiter": "cat <<1\n'\n1\n@@",
    "escaped ansi delimiter": "cat <<$'E\\x4fF'\nx\nEOF\n@@",
    "two heredocs": "cat <<A <<B\n'\nA\n'\nB\n@@",
    "zsh numeric operator": "[[ a == a<<-> ]]\n@@",
    "newline in a substitution": "cat <<EOF $(echo\n)\n'\nEOF\n@@",
    "body inside a substitution": "cat <<EOF $(\n'\nEOF\n)\n@@",
    "newline in an expansion": "cat <<EOF ${x:-\n}\n'\nEOF\n@@",
    "newline in arithmetic": "cat <<EOF $((1+\n1))\n'\nEOF\n@@",
    "newline in backticks": "cat <<EOF `echo\n`\n'\nEOF\n@@",
    "spaced delimiter": "cat <<' EOF'\nx\n EOF\n@@",
    "double quoted spaced delimiter": "cat <<\"END OF\" \nx\nEND OF\n@@",
    "trailing space delimiter": "cat <<'EOF '\nx\nEOF \n@@",
    "tab delimiter": "cat <<'\tEOF'\nx\n\tEOF\n@@",
    "escaped space delimiter": "cat <<E\\ \nx\nE \n@@",
    "substitution in a body": "cat <<EOF\n${x:-'} $(@@)\nEOF",
    "backticks in a body": "cat <<EOF\n$((`@@`+1))\nEOF",
    "substitution in a plain body": "cat <<EOF\n$(@@)\nEOF",
    "continued substitution quoted": "echo \"$\\\n(@@)\"",
    "continued substitution": "echo $\\\n(@@)",
    "continued expansion": "echo \"$\\\n{x:-$(@@)}\"",
    "nul in an ansi quote": "echo $'\\0'\n@@",
    "hex nul": "true $'\\x00' && @@",
    "nul after": "@@ $'\\0'",
    "quotes nested in a default": "true \"${x:-\"a ; b\"}\" && @@",
    "backticks in quoted arithmetic": "echo \"$(( `@@` ))\"",
    "backticks in a default in arithmetic": "echo $(( ${x:-`@@`} ))",
    "arithmetic in a default": "echo ${x:-$((`@@`))}",
    "backticks in an index": "(( a[`@@`] ))",
    "held by trap": "trap '$(@@)' EXIT",
    "held by trap in backticks": "trap 'echo `@@`' EXIT",
    "held in a variable": "x='$(@@)'; eval \"echo $x\"",
    "held in a subscript": "x='a[$(@@)]'; echo $((x))",
    "held by test": "test -v 'a[$(@@)]'",
    "held by printf": "printf -v 'a[$(@@)]' x",
    "held by read": "read 'a[$(@@)]' <<< x",
    "held by let": "let 'a[$(@@)]=1'",
    "held by a prompt": "PS4='$(@@)' bash -xc true",
    "held by find": "find . -maxdepth 0 -exec sh -c 'echo $(@@)' \\;",
    "held by env": "env FOO=1 sh -c 'echo $(@@)'",
    "held in a here-string": "bash <<< 'echo $(@@)'",
    "case in a substitution": "echo \"$(case a in a) @@;; esac)\"",
    "case with a quote": "echo \"$(case a in a) echo '\"';; esac)\"; @@",
    "command substitution braces": "echo ${ @@; }",
    "unterminated quote": "echo \"\n@@",
    "body closed with a parenthesis": "x=$(cat <<x\nfoo\nx)\n@@\nx",
    "comment inside backticks": "echo `echo a # b` ; @@",
    "quote inside backticks": "echo `echo 'a` ; @@ ; echo 'b'",
    "ansi quote inside backticks": "echo `echo $'a` ; @@ ; echo 'b'",
    "heredoc inside backticks": "echo `cat <<x\n`; @@;\nx\n`true`",
    "after an empty substitution": "``@@",
    "after a substitution that prints nothing": "$(true) @@",
    "after a comment in backticks": "`# x`@@",
    "after a closed descriptor": "exec 3>&-@@",
    "after a redirect bash ends at the mark": "<>! @@\ncat <<'X'",
}
"""Shapes a shell takes a command in, from the attack passes; `@@` is where the command stands."""

PAYLOADS = {
    "hook": f"rm {HOOK}",
    "key": f"cat {KEY}",
    "config": "git config core.hooksPath /tmp/x",
    "gh": "gh secret set X",
}


def _ground(tree: Path) -> unboxed.Ground:
    home = tree.parent / "home"
    return unboxed.Ground(str(tree), str(home), str(home / ".poolhouse"))


def cases(group: dict[str, str]):
    return pytest.mark.parametrize("command", list(group.values()), ids=list(group))


@pytest.mark.redteam
@cases(HIDDEN_BY_A_COMMENT)
def test_nothing_that_looks_like_a_comment_or_a_heredoc_hides_what_follows(command, tree) -> None:
    assert why(command, tree), command


@pytest.mark.redteam
@cases(GLUED_OPERATORS)
def test_operators_written_together_still_separate_commands(command, tree) -> None:
    assert why(command, tree), command


@pytest.mark.redteam
@cases(REDIRECT_SPELLINGS)
def test_every_spelling_of_a_redirect_to_a_file_is_a_write(command, tree) -> None:
    assert why(command, tree), command


@pytest.mark.redteam
@cases(COMPOUND_COMMANDS)
def test_a_command_inside_a_compound_command_is_judged(command, tree) -> None:
    assert why(command, tree), command


@pytest.mark.redteam
@cases(INTERPRETER_NAMES)
def test_an_interpreter_under_any_of_its_names_is_judged(command, tree) -> None:
    assert why(command, tree), command


@pytest.mark.redteam
@cases(HANDED_TO_A_PROGRAM)
def test_a_protected_path_handed_to_a_program_is_read(command, tree) -> None:
    assert why(command, tree), command


@pytest.mark.redteam
@pytest.mark.parametrize("payload", list(PAYLOADS.values()), ids=list(PAYLOADS))
@cases(SHAPES)
def test_the_shells_reading_alone_shows_the_command_in_every_shape(command, payload, tree) -> None:
    """Behind `UNSEEN` the line-by-line reading sees nothing, so each shape is judged by the shell's reading."""
    shaped = UNSEEN + command.replace("@@", payload)
    assert why(shaped, tree), shaped


@pytest.mark.redteam
@pytest.mark.parametrize("command", [
    "touch ${x:-a .git/hooks/pre-push b}", "cp x ${y:-.git/hooks/pre-push}", f"cat ${{x:-{KEY}}}",
    "gh pr merge 3 ${x:- --admin}", f"touch \"${{x:-\"a ; b\"}}\" {HOOK}"])
def test_the_words_of_an_unquoted_default_are_words_of_the_command(command, tree) -> None:
    assert why(UNSEEN + command, tree), command


@pytest.mark.redteam
@pytest.mark.parametrize("command", [
    "poolhouse-workspace inbox --agent claude-other",
    "echo \"$(echo \"a #\")\"; poolhouse-workspace inbox --agent claude-other",
    "poolhouse-workspace inbox ${x:- --agent claude-other }",
    "poolhouse-workspace inbox ${x:---agent=claude-other}",
    "env ${x:- POOLHOUSE_WORKSPACE_AGENT=claude-other } poolhouse-workspace inbox",
    "noglob poolhouse-workspace inbox ${x:---agent} claude-other",
    "true $'\\0'; poolhouse-workspace inbox --agent claude-other",
    "echo $((`poolhouse-workspace inbox --agent claude-other`))"])
def test_another_identity_is_seen_however_the_command_reads(command) -> None:
    assert protectedwrites.other_agent(UNSEEN + command, "claude-self"), command


@pytest.mark.redteam
@pytest.mark.parametrize("command", [
    "ls '<<X'\ngit push --force origin x", "ls # <<X\ngit add -A", "ls $((1<<x))\ngit push --force origin x",
    "noglob git push --force origin x", "! git commit --no-verify -m x", "{ git stash; }", "=git add -A",
    "git push \\\n --force origin x", "true $'\\0'; git push --force origin x", "true $'\\0' && git add -A",
    "wc -c <<< 'a**b'; git push --force origin x", "echo a**b; git push --force origin x"])
def test_the_bash_guards_own_rules_refuse_however_the_command_reads(command, tree) -> None:
    done = hook(command, tree)
    assert done.returncode == 2, (command, done.returncode, done.stderr)


@pytest.mark.redteam
@pytest.mark.parametrize("command", [
    f"echo {KEY[:0]}\x00; ls", "cat a\x00b; git push --force origin x", "cat \ud800", "echo " + "$(" * 3000 + ")" * 3000,
    "true " + "${x:-" * 3000, "echo " + "`" * 2001, "cat <<EOF\n" + "$(" * 2000, "ls " + "((" * 5000,
    "echo " + "x" * 300_000], ids=["nul", "nul in a word", "lone surrogate", "deep substitutions", "deep expansions",
                                   "backticks", "open body", "parentheses", "long"])
def test_no_command_makes_the_guard_fail_open(command, tree) -> None:
    done = hook(command, tree)
    assert done.returncode in (0, 2), (done.returncode, done.stderr[-600:])
    assert "git push" not in command or done.returncode == 2


@pytest.mark.redteam
@pytest.mark.parametrize("sent", [b"[1, 2]", b"\xff\xfe\x00garbage", b"{\"tool_name\": \"Bash\", \"tool_input\": 5}",
                                  b"{\"tool_name\": \"Bash\", \"tool_input\": {\"command\": 5}}", b"{\"n\": " + b"9" * 6000 + b"}"],
                         ids=["a list", "no text", "input that is a number", "command that is a number", "a huge number"])
def test_an_event_the_guard_fails_on_is_refused(sent, tree) -> None:
    done = subprocess.run([sys.executable, str(GUARD)], input=sent, capture_output=True, timeout=90,
                          env={**os.environ, "HOME": str(tree.parent / "home")})
    assert done.returncode == 2, (done.returncode, done.stderr[-600:])


FAILING = {"an error": "raise ValueError('x')", "an exit": "raise SystemExit(0)", "a loop": "while True: pass"}
if os.name != "nt":  # on Windows the clock is a thread, which a running match holds off: only a POSIX signal reaches it
    FAILING["a runaway match"] = "import re; re.match('(a|a)*$', 'a' * 64 + '!')"


@pytest.mark.redteam
@cases(FAILING)
def test_a_guard_that_fails_or_stalls_answers_two(command) -> None:
    code = (f"import sys; sys.path.insert(0, {str(HOOKS)!r}); import rules_loader\n"
            f"def decide():\n    {command}\n    return 0\n"
            "sys.exit(rules_loader.closed(decide, 'test', seconds=1.0))")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert done.returncode == 2 and "blocked" in done.stderr, (done.returncode, done.stderr)


@pytest.mark.redteam
def test_a_guard_leaves_no_thread_behind() -> None:
    """A thread still ending while the interpreter does crashes it, and the harness runs the call of a hook that crashed."""
    code = (f"import sys, threading; sys.path.insert(0, {str(HOOKS)!r}); import rules_loader\n"
            "sys.exit(rules_loader.closed(lambda: 2, 'test') + 10 * (threading.active_count() - 1))")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert done.returncode == 2, (done.returncode, done.stderr)


def _nested(depth: int) -> str:
    inner = "true"
    for _ in range(depth):
        inner = f"((: $( {inner}) ) )"
    return inner


def _twice_nested(depth: int) -> str:
    inner = "1" + "+0" * 2100
    for _ in range(depth):
        inner = f"$(( $( echo {inner} ) ))"
    return ": " + inner


SLOW = {
    "nested substitutions": _nested(40),
    "nested in both shells": _twice_nested(24),
    "many unclosed heredocs": "\n".join(f"cat <<A{i}" for i in range(8000)),
    "many open ansi quotes": "echo " + "$'\\'" * 5000,
    "many open expansions": "echo " + "${x:-" * 20000,
    "many operators": "a;" * 10000,
    "many bodies closed early": "x=$(cat <<A\n" * 5000 + "A)\n" * 5000 + "A\ntrue",
}


@pytest.mark.redteam
@cases(SLOW)
def test_reading_takes_bounded_time(command, tree) -> None:
    start = time.monotonic()
    done = hook(command + "; git push --force origin x", tree)
    assert done.returncode == 2 and "could not judge" not in done.stderr, done.stderr[-600:]
    assert time.monotonic() - start < 30


def test_the_pass_reads_each_character_once() -> None:
    from poolhouse import protectedshell

    small, large = _nested(200), _nested(3200)
    spent = []
    for text in (small, large):
        start = time.perf_counter()
        for _ in range(3):
            protectedshell.read(text)
        spent.append(time.perf_counter() - start)
    assert spent[1] < max(spent[0], 0.002) * 80, spent


HARMLESS = [
    f"ls # rm {HOOK}",
    "echo a#b",
    "echo '<<X'; ls",
    "ls $((1<<2))",
    "echo ${x:-#}",
    "echo $'it\\'s' && ls",
    "echo x >&2",
    "ls 2>&1 | head",
    "ls >& /tmp/out",
    "cat <<'EOF'\n$(never run)\nrm .git/hooks/pre-push\nEOF",
    "cat <<EOF > /tmp/x\nplain $HOME\nEOF",
    "cat <<-EOF\n\tgit stash\n\tEOF",
    "cat > f <<EOF\ncost \\$(x) and a ' quote\nEOF",
    "bash -c 'if true; then echo ok; fi'",
    "{ ls; echo; }",
    "python3.12 -c 'print(1)'",
    "case x in a) ls;; *) true;; esac",
    "for f in a b; do echo \"$f\"; done",
    "echo \"$(date \"+%s\")\"",
    "(cd /tmp && ls)\n\n\nls",
    "git commit -m \"$(cat <<'EOF'\nfix: it's (a) thing, no git stash or `git add -A`\n\nCo-Authored-By: x <x@agents.invalid>\nEOF\n)\"",
    "gh pr create --title t --body \"$(cat <<'EOF'\n## Summary\n- don't `run` $(this)\n)\n# git push --force\nEOF\n)\"",
    "echo $((1<<4)) && (( x = 1 << 2 ))",
    "grep -c '#' README.md; echo ${#x} $# a#b",
    "make 2>&1 | tee log; echo x >&2; exec 3>&-",
    "ls # don't\nls",
    "echo \"${msg:-it's done}\"",
    "printf '%s' $'a\\tb\\n'",
    "find . -name x -print0 | xargs -0 ls; IFS= read -r -d $'\\0' line < /dev/null",
    "while IFS= read -r -d $'\\0' f; do echo \"$f\"; done < <(find . -print0)",
    "jq . <<< '[" + ",".join(f'{{"a":{i},"b":{i}}}' for i in range(12)) + "]'",
    "python3 -m json.tool <<< '{\"k\": [" + ",".join(['{"x":1,"y":2}'] * 9) + "]}'",
    "wc -c <<< 'a**b'",
    "wc -c <<< '**bold** text'",
    "wc -c <<< '/**/*'",
    "cc -E -x c - <<< '/* note */ int x;'",
    "wc -c <<< '" + "word " * 30000 + "'",
    "curl -s localhost:1 -d '" + ",".join(['{"a":1,"b":2}'] * 3) + "'",
    "echo '**bold**'",
    "awk '{print $(NF-1), $1}' f | sed -E 's/\\(a|b)[0-9]+/x/' | grep -E '^(a|b)$'",
    "find . \\( -name '*.py' -o -name '*.md' \\) -print",
    "echo \"${path##*/} ${name//a/b} ${#list[@]} $((x % 2)) ($y)\"",
    "git log --format='%h (%s) # %an' -n 3; x=$(date +%s | head -c 4)",
    "ls a \\\n  b \\\n  c",
    "time -p ls",
]

PROSE = "git stash; git add -A; git push --force; rm .git/hooks/pre-push (it's `only` prose)"
DATA_IN_HEREDOCS = {
    "to a file": "cat <<'EOF' > notes.md\n@@\nEOF",
    "after a test": "[ -f x ] && cat <<'EOF' | tee y\n@@\nEOF\nls",
    "after a double bracket": "[[ -n $x ]] && cat > y <<'END_OF_FILE' && echo ok\n@@\nEND_OF_FILE",
    "after arithmetic": "n=$((n+1)); a[1]=2; (( n > 1 )) && cat <<'EOF'\n@@\nEOF",
    "indented": "if true; then\n  cat <<-'EOF'\n\t@@\n\tEOF\nfi",
    "in a function": "f() {\n  cat <<'EOF'\n@@\nEOF\n}",
    "in a loop": "for x in \"${a[@]}\"; do cat <<\"EOF\"\n@@\nEOF\ndone",
    "in a case": "case $x in\n  a) cat <<'EOF'\n@@\nEOF\n  ;;\nesac",
    "in a subshell": "(cat <<'A'\n@@\nA\n)",
    "in a substitution": "x=$(cat <<'EOF'\n@@\nEOF\n)\necho \"$x\"",
    "after backticks": "echo `date`; cat <<'EOF'\n@@\nEOF",
    "after comments": "# don't (stop\ncat <<'EOF' >> y # it's\n@@\nEOF",
}


@pytest.mark.parametrize("command", HARMLESS, ids=range(len(HARMLESS)))
def test_ordinary_shell_text_is_not_refused(command, tree) -> None:
    done = hook(command, tree)
    assert done.returncode == 0, (command[:200], done.stderr[-600:])
    assert not why(command, tree), command[:200]
    assert not protectedwrites.other_agent(command, "claude-self")


@cases(DATA_IN_HEREDOCS)
def test_the_data_of_a_heredoc_is_not_refused(command, tree) -> None:
    shaped = command.replace("@@", PROSE)
    done = hook(shaped, tree)
    assert done.returncode == 0, (shaped, done.stderr[-600:])


@pytest.mark.parametrize("hook_name", ["claude-bash-guard", "claude-tool-guard", "rules_loader.py"])
def test_a_guard_evaluates_no_annotation_an_older_python_cannot(hook_name) -> None:
    """A hook runs under whatever `python3` its harness finds, and one that fails to load there lets every call run."""
    source = (HOOKS / hook_name).read_text(encoding="utf-8")
    flags = compile(source, hook_name, "exec", dont_inherit=True).co_flags
    assert flags & __future__.annotations.compiler_flag, hook_name


@pytest.mark.parametrize("command", [f"git commit -m 'docs: {KEY}'", f"git -C . commit --message '{KEY}'", "grep x <<< text",
                                     f"true # cat {KEY}", f"git status\n# cat {KEY}\nls"])
def test_a_commit_message_and_a_plain_here_string_run(command, tree) -> None:
    assert not why(command, tree), command


@pytest.mark.parametrize("command", ["x=$(cd /etc && pwd); rm -f build.log", "echo \"$(cd / && ls)\" > listing.txt", "ls # note"])
def test_the_delegate_blocks_leave_ordinary_work_in_the_project(command, tree) -> None:
    assert not unboxed.refusal("Bash", {"command": command}, _ground(tree)), command


@pytest.mark.redteam
@pytest.mark.parametrize("command", ["ls '<<X'\nrm -rf /Users/x/elsewhere", "noglob rm -rf /Users/x/elsewhere",
                                     "{ rm -rf /Users/x/elsewhere }", "echo ${x:- #}; touch /Users/x/elsewhere"])
def test_the_delegate_blocks_judge_every_reading(command, tree) -> None:
    ground = _ground(tree)
    assert unboxed.refusal("Bash", {"command": command}, ground), command
