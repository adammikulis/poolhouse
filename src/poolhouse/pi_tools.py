"""Pi built-in tools executed in isolated OS sandbox processes."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from poolhouse import executable_libraries, sandbox
from poolhouse.sandbox import policies

MAX_FRAME = 4 * 1024 * 1024
TOOLS = ("read", "bash", "edit", "write", "grep", "find", "ls")
CONTROL_PATHS = (".git/config", ".git/config.worktree", ".git/hooks", ".claude/settings.json",
                 ".claude/settings.local.json", ".codex/config.toml", ".pi/settings.json", ".mcp.json")


def _inspect(command: list[str]) -> tuple[int, str]:
    done = subprocess.run(command, env=policies.system_env(HOME="/var/empty"), cwd="/",
                          capture_output=True, timeout=10, text=True, check=False)
    if len(done.stdout.encode()) > MAX_FRAME:
        raise ValueError("The executable library report exceeds its limit")
    return done.returncode, done.stdout

WORKER = '''import {readFileSync} from "node:fs";
import {pathToFileURL} from "node:url";
const [modulePath, root] = process.argv.slice(2);
const api = await import(pathToFileURL(modulePath).href);
const factories = {read:api.createReadToolDefinition,bash:api.createBashToolDefinition,
 edit:api.createEditToolDefinition,write:api.createWriteToolDefinition,
 grep:api.createGrepToolDefinition,find:api.createFindToolDefinition,ls:api.createLsToolDefinition};
try {
 const frame = JSON.parse(readFileSync(0,"utf8"));
 if (!Object.hasOwn(factories,frame.tool)) throw Error("Unknown coding tool");
 const tool = factories[frame.tool](root);
 const result = await tool.execute("isolated",frame.input);
 process.stdout.write(JSON.stringify(result));
} catch(error) {
 process.stdout.write(JSON.stringify({isError:true,content:[{type:"text",text:String(error.message)}]}));
}
'''


def runtime(binary: str) -> dict:
    """Resolve the selected installed Pi package and its Node runtime reads."""
    package = Path(binary).resolve().parents[2]
    metadata = json.loads((package / "package.json").read_text())
    if metadata.get("name") != "@earendil-works/pi-coding-agent":
        raise ValueError("The selected Pi runtime is not a supported installed package")
    module = package / "dist" / "index.js"
    if not module.is_file():
        raise ValueError("The installed Pi tool library is missing")
    found = shutil.which("node")
    if not found:
        raise ValueError("The Pi Node runtime is not installed")
    node = os.path.realpath(found)
    env = policies.system_env(HOME="/var/empty")
    reported = subprocess.run([node, "-e", "process.stdout.write(JSON.stringify(process.report.getReport().sharedObjects))"],
                              env=env, cwd="/", capture_output=True, check=True, timeout=10)
    if len(reported.stdout) > MAX_FRAME:
        raise ValueError("The Node runtime library report exceeds its limit")
    libraries = json.loads(reported.stdout)
    if not isinstance(libraries, list) or not all(isinstance(path, str) and Path(path).is_absolute() for path in libraries):
        raise ValueError("The Node runtime library report is invalid")
    aliases = []
    executables = [path for path in (node, *libraries) if Path(path).is_file()
                   and not path.startswith(("/usr/lib", "/System", "/lib"))]
    declared = set(executable_libraries.paths(executables, _inspect))
    for library in declared:
        path = Path("/")
        for part in Path(library).parts[1:]:
            path /= part
            if path.is_symlink():
                aliases.append(str(path))
                break
        real_parent = Path(library).parent.resolve()
        leaf = real_parent / Path(library).name
        if leaf.is_symlink():
            aliases.append(str(leaf))
    return {"node": node, "module": str(module), "metadata": list(dict.fromkeys(aliases)),
        "reads": list(dict.fromkeys([str(package), str(Path(node).parent.parent),
            *(os.path.realpath(path) for path in libraries if Path(path).exists())]))}


def prepared(files, binary: str, root: Path, role: str) -> Path:
    """Write the fixed worker and its launcher-owned configuration."""
    selected = runtime(binary)
    worker = files.write("tool-worker.mjs", WORKER)
    canonical = sandbox.checked_path(root, what="coding repository")
    for path in [*selected["reads"], str(worker.parent)]:
        candidate = Path(path)
        if candidate == root or candidate in root.parents or root in candidate.parents:
            raise ValueError("The coding repository and trusted tool runtime must not overlap")
    return files.write("tool-worker.json", json.dumps({**selected, "worker": str(worker),
        "root": canonical, "tool_root": canonical, "write": role != "read-only"}, sort_keys=True))


def execute(configuration: dict, frame: dict) -> dict:
    """Execute a built-in tool with only its repository and scratch grants."""
    if set(frame) != {"tool", "input"} or frame["tool"] not in TOOLS or not isinstance(frame["input"], dict):
        raise ValueError("Invalid coding tool request")
    payload = json.dumps(frame).encode()
    if len(payload) > MAX_FRAME:
        raise ValueError("The coding tool request exceeds its limit")
    root = sandbox.checked_path(configuration["root"], what="coding repository")
    node = sandbox.checked_path(configuration["node"], what="Node runtime")
    controls = [str(Path(root) / path) for path in CONTROL_PATHS if not (
        path.startswith(".git/") and (Path(root) / ".git").is_file())]
    if (Path(root) / ".git").is_file():
        controls.append(str(Path(root) / ".git"))
    with policies.scratch("poolhouse-coding-tool-") as scratch:
        writable = (root, scratch) if configuration["write"] else (scratch,)
        policy = sandbox.Policy("coding-tool", read=(root, configuration["worker"], *configuration["reads"]),
            write=writable, exec=(node, root, scratch, *policies.SYSTEM_EXEC), strict=True,
            deny_write=tuple(controls),
            read_metadata=(*configuration["metadata"], configuration["tool_root"]),
            env=policies.system_env(HOME=scratch, TMPDIR=scratch, PI_CODING_AGENT_DIR=scratch, OPENSSL_CONF=os.devnull),
            limits=sandbox.Limits(wall_seconds=120, output_bytes=MAX_FRAME))
        result = sandbox.run([node, configuration["worker"], configuration["module"], configuration["tool_root"]], policy,
                             cwd=root, stdin=payload)
        if result.truncated or result.timed_out or not result.ok:
            raise ValueError("The isolated coding tool stopped: " + result.stderr[:500])
        answer = json.loads(result.stdout)
        if not isinstance(answer, dict) or not isinstance(answer.get("content"), list):
            raise ValueError("The coding tool returned an invalid result")
        return answer


def run() -> int:
    try:
        if len(sys.argv) != 2:
            raise ValueError("A launcher-owned tool configuration is required")
        configuration = json.loads(Path(sys.argv[1]).read_text())
        raw = sys.stdin.buffer.read(MAX_FRAME + 1)
        if len(raw) > MAX_FRAME:
            raise ValueError("The coding tool request exceeds its limit")
        answer = execute(configuration, json.loads(raw))
    except (OSError, ValueError, sandbox.SandboxUnavailable) as error:
        answer = {"isError": True, "content": [{"type": "text", "text": "poolhouse: " + str(error)}]}
    sys.stdout.write(json.dumps(answer) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
