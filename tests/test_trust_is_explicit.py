"""Every pull of a model and every download in the repository outside the tests names the trust or the pipeline it
uses, and nothing outside the facade modules names, builds or looks up the allowing trust (`LIBRARY`, `Trust(...)`)
or the allowing pipeline (`net.library`, `ANYWHERE`), whatever the owner of the call is called: `ph.models.pull`,
`ph.hub.fetch`, `import poolhouse.net`, an aliased import, a star import, a name read from a string.

The scan is a function over source text, and the tests below feed it code that breaks each rule to show it says so."""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", ".venv", "venv", "node_modules", "target", "dist", "build", "__pycache__", "tests", ".pytest_cache"}

TRUST_FACADES = {"src/poolhouse/hub/admission.py", "src/poolhouse/hub/__init__.py"}
NET_FACADES = {"src/poolhouse/net/policy.py", "src/poolhouse/net/pipeline.py", "src/poolhouse/net/__init__.py"}
PASSES_ON = {"src/poolhouse/api/models.py", "src/poolhouse/hub/listing.py", "src/poolhouse/hub/transfer.py"}

PULLS = {f"poolhouse.{where}.{name}" for where in ("hub", "hub.transfer", "hub.listing", "models", "api.models")
         for name in ("pull", "fetch", "snapshot", "base_folder")}
DOWNLOADS = {"poolhouse.net.get", "poolhouse.net.download", "poolhouse.net.download.download"}
BUILDS = {f"poolhouse.{where}.Trust" for where in ("hub", "hub.admission", "models", "api.models")}
ALLOWING = {"poolhouse.hub.LIBRARY", "poolhouse.hub.admission.LIBRARY", "poolhouse.models.LIBRARY",
            "poolhouse.api.models.LIBRARY", "poolhouse.net.library", "poolhouse.net.pipeline.library",
            "poolhouse.net.policy.ANYWHERE", "poolhouse.net.ANYWHERE"}
STARRED = {"poolhouse.hub", "poolhouse.hub.admission", "poolhouse.net", "poolhouse.net.pipeline", "poolhouse.net.policy",
           "poolhouse.models", "poolhouse.api.models"}
TRUSTS = {"PERSON", "hub.PERSON", "admission.PERSON"}
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


def keyword(call: ast.Call, name: str) -> str:
    return next((ast.unparse(k.value) for k in call.keywords if k.arg == name), "")


def scan(source: str, path: str) -> list[str]:
    """Every way ``source`` (the file at repository-relative ``path``) breaks the rules, as ``path:line what``."""
    tree = ast.parse(source)
    names, found = Names(tree, path), []

    def flag(node: ast.AST | int, what: str) -> None:
        found.append(f"{path}:{node if isinstance(node, int) else node.lineno} {what}")

    trust_door, net_door = path in TRUST_FACADES, path in NET_FACADES

    def opened(target: str) -> bool:
        """Whether this file is a facade for ``target``."""
        return (trust_door and target.endswith("LIBRARY")) or (net_door and not target.endswith("LIBRARY"))

    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME and token.string in {"LIBRARY", "ANYWHERE"} and not opened(token.string):
            flag(token.start[0], f"names {token.string}")
    for star in names.stars:
        if star in STARRED and not (trust_door or net_door):
            flag(1, f"star-imports {star}, which lists the allowing names")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value in {"LIBRARY", "ANYWHERE"} and not opened(str(node.value)):
            flag(node, f"names {node.value!r} as a string")
        if isinstance(node, ast.Call) and looks_up(node) and not net_door:
            flag(node, "looks up an allowing name by its string")
        if isinstance(node, ast.ImportFrom):
            for one in node.names:
                if (target := f"{names.base(node)}.{one.name}") in ALLOWING and not opened(target):
                    flag(node, f"imports {one.name}")
        if isinstance(node, ast.Attribute | ast.Name) and names.of(node) in ALLOWING and not opened(names.of(node)):
            flag(node, f"reaches {names.of(node)}")
        if not isinstance(node, ast.Call):
            continue
        target = names.of(node.func)
        if target in BUILDS and not trust_door:
            flag(node, "builds a Trust")
        if target == "dataclasses.replace" and node.args and ast.unparse(node.args[0]).endswith("PERSON"):
            flag(node, "loosens PERSON")
        if target in PULLS:
            given = keyword(node, "trust")
            if not given:
                flag(node, f"{target} names no trust")
            elif given not in TRUSTS and not (given == "trust" and path in PASSES_ON):
                flag(node, f"{target} passes trust={given}")
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


def test_every_pull_and_download_names_its_trust_and_nothing_outside_the_facades_names_the_allowing_ones():
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


def test_a_pull_without_a_trust_is_flagged_whatever_the_owner_is_called():
    for code in ("import poolhouse as ph\nph.models.pull('a/b', 'c')",
                 "import poolhouse as p\np.hub.fetch('hf:a/b/c')",
                 "from poolhouse import hub\nhub.pull('hf:a/b')",
                 "from poolhouse import hub as h\nh.snapshot('a/b')",
                 "import poolhouse.hub\npoolhouse.hub.base_folder('a/b')",
                 "from poolhouse.hub import fetch as get_model\nget_model('hf:a/b/c')",
                 "from poolhouse.hub.transfer import pull\npull('hf:a/b')",
                 "from poolhouse.api import models\nmodels.pull('a/b', 'c')",
                 "from poolhouse.api.models import pull as take\ntake('a/b', 'c')",
                 "from poolhouse.hub import listing\nlisting.fetch('hf:a/b/c')"):
        assert any("names no trust" in one for one in flagged(code)), code


def test_a_pull_that_names_a_trust_other_than_the_person_one_is_flagged():
    for code in ("import poolhouse as ph\nph.models.pull('a/b', 'c', trust=ph.models.Trust())",
                 "from poolhouse import hub\nhub.pull('hf:a/b', trust=options)",
                 "from poolhouse import hub\nhub.pull('hf:a/b', trust=trust)"):
        assert flagged(code), code
    assert flagged("from poolhouse import hub\nhub.pull('hf:a/b', trust=hub.PERSON)") == []
    assert flagged("from poolhouse.hub.admission import PERSON\nfrom poolhouse import hub\nhub.fetch('hf:a/b/c', trust=PERSON)") == []


def test_a_trust_may_be_passed_on_only_by_the_functions_that_take_it_as_an_argument():
    code = "from poolhouse import hub\ndef f(trust):\n    hub.fetch('hf:a/b/c', trust=trust)\n"
    assert flagged(code, "src/poolhouse/serve/y.py") and flagged(code, "src/poolhouse/api/models.py") == []


def test_the_allowing_trust_is_flagged_by_name_by_import_by_attribute_and_by_string():
    for code in ("from poolhouse.hub import LIBRARY\n",
                 "from poolhouse.hub.admission import LIBRARY as ANY\n",
                 "from poolhouse import hub\nx = hub.LIBRARY\n",
                 "import poolhouse as ph\nx = ph.hub.LIBRARY\n",
                 "import poolhouse.hub.admission as adm\nx = adm.LIBRARY\n",
                 "from poolhouse.hub import *\n",
                 "from poolhouse import hub\nx = getattr(hub, 'LIBRARY')\n",
                 "from poolhouse.hub import Trust\nx = Trust()\n",
                 "from poolhouse.hub.admission import Trust as T\nx = T(publishers=None)\n",
                 "import poolhouse as ph\nx = ph.models.Trust()\n",
                 "import dataclasses\nfrom poolhouse.hub.admission import PERSON\nx = dataclasses.replace(PERSON, ask=False)\n"):
        assert flagged(code), code


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
    assert flagged("from . import transfer\ntransfer.pull('hf:a/b')", "src/poolhouse/hub/x.py")
    assert flagged("from .transfer import pull\npull('hf:a/b')", "src/poolhouse/hub/x.py")
    assert flagged("def snapshot(r):\n    return r\nsnapshot('a/b')\n", "src/poolhouse/hub/transfer.py")
    assert flagged("def snapshot(r):\n    return r\nsnapshot('a/b')\n", "src/poolhouse/other/y.py") == []


def test_a_call_that_is_not_a_pull_is_left_alone():
    assert flagged("class Member:\n    def pull(self, x): ...\nMember().pull('a')\nm = Member()\nm.pull('b')") == []
    assert flagged("import subprocess\nsubprocess.run(['git', 'pull'])\nlibrary = 3\nx = library\n") == []
