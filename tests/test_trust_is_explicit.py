"""No pull of a model in the repository outside the tests says who asked for it (a pull's trust is the process's: an
agent marker in its environment makes the pull an agent's, and only the app's own click passes `PERSON`), every
download names the pipeline it uses, and nothing outside the facade modules names, builds or looks up the allowing
pipeline (`net.library`, `ANYWHERE`), whatever the owner of the call is called: `ph.models.pull`,
`ph.hub.fetch`, `import poolhouse.net`, an aliased import, a star import, a name read from a string.

The scan is a function over source text, and the tests below feed it code that breaks each rule to show it says so.
It stops accidents, not attackers: a name built at run time (``getattr(module, name)``, ``vars``) passes it."""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", ".venv", "venv", "node_modules", "target", "dist", "build", "__pycache__", "tests", ".pytest_cache"}

NET_FACADES = {"src/poolhouse/net/policy.py", "src/poolhouse/net/pipeline.py", "src/poolhouse/net/__init__.py"}
PASSES_ON = {"src/poolhouse/api/models.py", "src/poolhouse/hub/listing.py", "src/poolhouse/hub/transfer.py"}

PULLS = {f"poolhouse.{where}.{name}" for where in ("hub", "hub.transfer", "hub.listing", "models", "api.models")
         for name in ("pull", "fetch", "snapshot", "base_folder")} | {"poolhouse.hub.hf_cache.fetch"}
DOWNLOADS = {"poolhouse.net.get", "poolhouse.net.download", "poolhouse.net.download.download"}
POLICIES = {"poolhouse.net.Policy", "poolhouse.net.policy.Policy"}
ALLOWING = {"poolhouse.net.library", "poolhouse.net.pipeline.library",
            "poolhouse.net.policy.ANYWHERE", "poolhouse.net.ANYWHERE"}
STARRED = {"poolhouse.net", "poolhouse.net.pipeline", "poolhouse.net.policy"}
PIPELINES = {"net.default()"}


def module_of(path: str) -> str:
    """The dotted module a ``src/`` path is, else ''."""
    parts = Path(path).with_suffix("").parts
    if parts[:2] != ("src", "poolhouse"):
        return ""
    return ".".join(parts[1:-1] if parts[-1] == "__init__" else parts[1:])


class Names:
    """What each name bound by an import or a top-level definition in one module stands for."""

    def __init__(self, tree: ast.Module, path: str) -> None:
        self.module, self.stars = module_of(path), []
        self.package = self.module if path.endswith("__init__.py") else self.module.rpartition(".")[0]
        self.bound: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for one in node.names:
                    root = one.name.split(".")[0]
                    self.bound[one.asname or root] = one.name if one.asname else root
            elif isinstance(node, ast.ImportFrom):
                base = self.base(node)
                for one in node.names:
                    if one.name == "*":
                        self.stars.append(base)
                    else:
                        self.bound[one.asname or one.name] = f"{base}.{one.name}"
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and self.module:
                self.bound.setdefault(node.name, f"{self.module}.{node.name}")

    def base(self, node: ast.ImportFrom) -> str:
        if not node.level:
            return node.module or ""
        up = self.package.split(".")
        up = up[:len(up) - (node.level - 1)] if node.level > 1 else up
        return ".".join([*up, *([node.module] if node.module else [])])

    def of(self, expr: ast.AST) -> str:
        """The dotted name ``expr`` stands for, or ''."""
        tail: list[str] = []
        while isinstance(expr, ast.Attribute):
            tail.append(expr.attr)
            expr = expr.value
        if not isinstance(expr, ast.Name) or expr.id not in self.bound:
            return ""
        return ".".join([self.bound[expr.id], *reversed(tail)])


def looks_up(call: ast.Call) -> bool:
    """Whether ``call`` is ``getattr``, ``hasattr`` or ``attrgetter`` of the name ``library``."""
    named = ast.unparse(call.func).rsplit(".", 1)[-1] in {"getattr", "hasattr", "attrgetter"}
    return named and any(isinstance(a, ast.Constant) and a.value == "library" for a in call.args)


def wildcard_only(node: ast.AST) -> bool:
    """Whether ``node`` is a string of wildcards alone (``*``, ``*.*``, ``?*``): a host pattern that matches every host."""
    text = node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""
    return bool(text) and set(text) <= set("*?.[]!-") and bool(set(text) & set("*?"))


def keyword(call: ast.Call, name: str) -> str:
    return next((ast.unparse(k.value) for k in call.keywords if k.arg == name), "")


def scan(source: str, path: str) -> list[str]:
    """Every way ``source`` (the file at repository-relative ``path``) breaks the rules, as ``path:line what``."""
    tree = ast.parse(source)
    names, found = Names(tree, path), []

    def flag(node: ast.AST | int, what: str) -> None:
        found.append(f"{path}:{node if isinstance(node, int) else node.lineno} {what}")

    net_door = path in NET_FACADES

    def opened() -> bool:
        """Whether this file is a facade for the allowing names."""
        return net_door

    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME and token.string == "ANYWHERE" and not opened():
            flag(token.start[0], f"names {token.string}")
    for star in names.stars:
        if star in STARRED and not net_door:
            flag(1, f"star-imports {star}, which lists the allowing names")
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute | ast.Name) and id(node) not in called
                and names.of(node) in PULLS | DOWNLOADS and path not in PASSES_ON | NET_FACADES):
            flag(node, f"passes {names.of(node)} around without calling it")
        if isinstance(node, ast.Constant) and node.value == "ANYWHERE" and not opened():
            flag(node, f"names {node.value!r} as a string")
        if isinstance(node, ast.Call) and looks_up(node) and not net_door:
            flag(node, "looks up an allowing name by its string")
        if isinstance(node, ast.ImportFrom):
            for one in node.names:
                if (target := f"{names.base(node)}.{one.name}") in ALLOWING and not opened():
                    flag(node, f"imports {one.name}")
        if isinstance(node, ast.Attribute | ast.Name) and names.of(node) in ALLOWING and not opened():
            flag(node, f"reaches {names.of(node)}")
        if not isinstance(node, ast.Call):
            continue
        target = names.of(node.func)
        if target in POLICIES and not net_door and any(
                wildcard_only(c) for a in (*node.args, *(k.value for k in node.keywords)) for c in ast.walk(a)):
            flag(node, "builds a Policy that lists every host")
        if target in PULLS and (given := keyword(node, "trust")) and not (given == "trust" and path in PASSES_ON):
            flag(node, f"{target} says who asked with trust={given}")
        if target in DOWNLOADS and path not in NET_FACADES:
            given = keyword(node, "pipeline")
            if not given:
                flag(node, f"{target} names no pipeline")
            elif given not in PIPELINES and not (given in {"pipeline", "pipe"} and path in PASSES_ON | NET_FACADES):
                flag(node, f"{target} passes pipeline={given}")
    return found


def python_files():
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if not path.is_file() or SKIP & set(relative.parts):
            continue
        if path.suffix == ".py" or (relative.parts[0] == "scripts" and not path.suffix and path.read_bytes()[:20].startswith(b"#!")
                                    and b"python" in path.read_bytes()[:80].split(b"\n")[0]):
            yield relative.as_posix(), path


def test_no_pull_says_who_asked_and_every_download_names_its_pipeline_and_nothing_names_the_allowing_ones():
    problems, scanned = [], 0
    for relative, path in python_files():
        problems += scan(path.read_text(encoding="utf-8"), relative)
        scanned += 1
    assert scanned > 500
    assert not problems, "\n".join(problems)


def test_the_scan_sees_the_pulls_and_downloads_there_are():
    pulls = downloads = 0
    for relative, path in python_files():
        if not relative.startswith("src/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = Names(tree, relative)
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            pulls += names.of(call.func) in PULLS
            downloads += names.of(call.func) in DOWNLOADS and relative not in NET_FACADES
    assert pulls >= 12 and downloads >= 9, (pulls, downloads)


# -- the scan itself: each rule broken in each way an owner can be spelled ----------------------
def flagged(code: str, path: str = "src/poolhouse/x/y.py") -> list[str]:
    return scan(code, path)


def test_a_pull_that_says_who_asked_is_flagged_whatever_the_owner_is_called():
    for code in ("import poolhouse as ph\nph.models.pull('a/b', 'c', trust=ph.models.Trust.PERSON)",
                 "import poolhouse as p\np.hub.fetch('hf:a/b/c', trust=p.hub.PERSON)",
                 "from poolhouse import hub\nhub.pull('hf:a/b', trust=hub.PERSON)",
                 "from poolhouse import hub as h\nh.snapshot('a/b', trust=h.PERSON)",
                 "from poolhouse.hub.admission import PERSON\nfrom poolhouse import hub\nhub.fetch('hf:a/b/c', trust=PERSON)",
                 "from poolhouse.hub import fetch as get_model\nget_model('hf:a/b/c', trust=options)",
                 "from poolhouse.hub.transfer import pull\npull('hf:a/b', trust=trust)",
                 "from poolhouse.hub import hf_cache\nhf_cache.fetch('a/b', trust=hub.PERSON)\n"):
        assert any("says who asked" in one for one in flagged(code)), code
    for code in ("import poolhouse as ph\nph.models.pull('a/b', 'c')",
                 "from poolhouse import hub\nhub.pull('hf:a/b')",
                 "from poolhouse.hub import hf_cache\nhf_cache.fetch('a/b')\n"):
        assert flagged(code) == [], code


def test_a_trust_may_be_passed_on_only_by_the_functions_that_take_it_as_an_argument():
    code = "from poolhouse import hub\ndef f(trust):\n    hub.fetch('hf:a/b/c', trust=trust)\n"
    assert flagged(code, "src/poolhouse/serve/y.py") and flagged(code, "src/poolhouse/api/models.py") == []


def test_the_allowing_pipeline_is_flagged_by_name_by_import_by_attribute_and_by_string():
    for code in ("from poolhouse import net\nnet.get('https://a.example', pipeline=net.library())",
                 "import poolhouse.net\nx = poolhouse.net.library()",
                 "import poolhouse as ph\nx = ph.net.library()",
                 "from poolhouse.net import library\n",
                 "from poolhouse.net import library as anywhere\nanywhere()",
                 "import poolhouse.net.pipeline as np\nx = np.library()",
                 "from poolhouse.net.policy import ANYWHERE\n",
                 "from poolhouse.net import policy\nx = policy.ANYWHERE\n",
                 "from poolhouse.net import *\n",
                 "from poolhouse import net\nx = getattr(net, 'library')\n"):
        assert flagged(code), code


def test_a_policy_that_lists_every_host_is_flagged_however_it_is_spelled():
    for code in ("from poolhouse import net\nnet.Policy(allowed=('*',))",
                 "from poolhouse.net import Policy\nPolicy(['api.acme.example', '*'])",
                 "import poolhouse as ph\nph.net.Policy(allowed=('*',))",
                 "from poolhouse.net.policy import Policy as P\nP(allowed=['*'])",
                 "from poolhouse import net\nnet.Policy(('*.*',))",
                 "from poolhouse import net\nnet.Policy(allowed=('api.acme.example', '?*'))",
                 "from poolhouse import net\nnet.Policy(allowed=['**'])"):
        assert any("lists every host" in one for one in flagged(code)), code
    assert flagged("from poolhouse import net\nnet.Policy(allowed=('*.acme.example',))") == []


def test_a_download_without_a_pipeline_is_flagged_whatever_the_owner_is_called():
    for code in ("from poolhouse import net\nnet.download('https://a.example/x', 'x')",
                 "import poolhouse.net\npoolhouse.net.download('https://a.example/x', 'x')",
                 "import poolhouse as ph\nph.net.get('https://a.example/x')",
                 "from poolhouse.net import download\ndownload('https://a.example/x', 'x')",
                 "from poolhouse.net import download as fetch_file\nfetch_file('https://a.example/x', 'x')",
                 "from poolhouse.net.download import download\ndownload('https://a.example/x', 'x')"):
        assert any("names no pipeline" in one for one in flagged(code)), code
    assert flagged("from poolhouse import net\nnet.download('https://a.example/x', 'x', pipeline=net.default())") == []
    assert flagged("from poolhouse import net\nnet.get('https://a.example/x', pipeline=other)")


def test_the_scan_follows_a_relative_import_and_a_name_defined_in_the_module():
    assert flagged("from . import transfer\ntransfer.pull('hf:a/b', trust=x)", "src/poolhouse/hub/x.py")
    assert flagged("from .transfer import pull\npull('hf:a/b', trust=x)", "src/poolhouse/hub/x.py")
    assert flagged("def snapshot(r, trust):\n    return r\nsnapshot('a/b', trust=1)\n", "src/poolhouse/hub/transfer.py")
    assert flagged("def snapshot(r, trust):\n    return r\nsnapshot('a/b', trust=1)\n", "src/poolhouse/other/y.py") == []


def test_a_call_that_is_not_a_pull_is_left_alone():
    assert flagged("class Member:\n    def pull(self, x): ...\nMember().pull('a')\nm = Member()\nm.pull('b')") == []
    assert flagged("import subprocess\nsubprocess.run(['git', 'pull'])\nlibrary = 3\nx = library\n") == []


def test_a_pull_or_a_download_passed_around_without_a_call_is_flagged():
    for code in ("from poolhouse import hub\nf = hub.pull\nf('hf:a/b')",
                 "import poolhouse as ph\nf = ph.models.pull\n",
                 "from poolhouse import net\nrun = net.download\n",
                 "from poolhouse.hub import fetch\nx = [fetch]\n"):
        assert any("around without calling it" in one for one in flagged(code)), code
    assert flagged("from poolhouse import hub\nhub.pull('hf:a/b')") == []
    assert flagged("from poolhouse.hub import fetch\nx = [fetch]\n", "src/poolhouse/api/models.py") == []
