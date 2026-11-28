# The workloads that are timed twice: natively (the same files under chroot on the same processor) and inside the
# virtual machine. Runs in the guest system with /opt/bench (Lua's source) and /opt/poolhouse (this repository).
# A line `ONLY="name name"` put before this script times just those workloads.
export ONLY="${ONLY:-}"
cat > /tmp/bench.py <<'EOF'
import os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

JOBS = "3"
work = Path(tempfile.mkdtemp(dir="/root"))

only = os.environ["ONLY"].split()

def timed(name, command, cwd=work):
    if only and name not in only:
        return
    began = time.monotonic()
    done = subprocess.run(command, cwd=cwd, shell=isinstance(command, str), capture_output=True, text=True)
    took = time.monotonic() - began
    tail = (done.stdout + done.stderr).strip().splitlines()[-1:] or [""]
    print(f"bench {name} {took:.2f} s exit {done.returncode} | {tail[0][:150]}", flush=True)

print("bench host", os.uname().release, os.cpu_count(), "processors", flush=True)
timed("python_arithmetic", [sys.executable, "-c", "print(sum(i * i % 7 for i in range(20_000_000)))"])
timed("start_300_programs", [sys.executable, "-c",
      "import subprocess\nfor _ in range(300): subprocess.run(['/bin/true'], check=True)\nprint('done')"])
timed("write_read_3000_files", [sys.executable, "-c",
      "import os, pathlib\nroot = pathlib.Path('many'); root.mkdir()\n"
      "for i in range(3000): (root / str(i)).write_bytes(os.urandom(8192))\n"
      "print(sum(len(p.read_bytes()) for p in root.iterdir()))"])
shutil.copytree("/opt/bench/lua-5.4.7", work / "lua")
timed("c_compile_one_file", ["gcc", "-O2", "-std=gnu99", "-DLUA_USE_POSIX", "-c", "lua/src/lvm.c", "-o", "lvm.o"])
timed("c_build_lua_j3", ["make", "-C", "lua/src", f"-j{JOBS}", "all", "SYSCFLAGS=-DLUA_USE_POSIX"])
timed("c_built_program_runs", "lua/src/lua -e 'local s=0 for i=1,30000000 do s=s+i%7 end print(s)'")
parts = []
for n in range(120):
    parts.append(f"#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Default)]\n"
                 f"pub struct R{n} {{ pub a: u64, pub b: String, pub c: Vec<u32> }}\n"
                 f"pub fn f{n}(x: &[R{n}]) -> u64 {{ let mut m = std::collections::BTreeMap::new();\n"
                 f"  for r in x {{ m.entry(r.clone()).or_insert_with(Vec::new).push(r.a); }}\n"
                 f"  m.values().map(|v| v.iter().sum::<u64>()).sum() }}\n")
calls = "".join(f"    t += f{n}(&[R{n}::default(), R{n} {{ a: {n}, ..Default::default() }}]);\n" for n in range(120))
(work / "main.rs").write_text("".join(parts) + "fn main() {\n    let mut t = 0;\n" + calls + "    println!(\"{}\", t);\n}\n")
timed("rust_build_optimized", ["rustc", "--edition", "2021", "-C", "opt-level=2", "-C", f"codegen-units={JOBS}",
      "-o", "rusty", "main.rs"])
timed("rust_built_program_runs", "./rusty")
timed("git_commit_repository", "rm -rf repo && cp -a /opt/poolhouse repo && cd repo && git init -q && "
      "git add -A && git -c user.email=bench@localhost -c user.name=bench commit -qm bench && git ls-files | wc -l")
timed("python_test_suite_part", [sys.executable, "-m", "test", "-j" + JOBS, "test_json", "test_re", "test_zipfile",
      "test_tarfile", "test_subprocess", "test_decimal", "test_pickle", "test_asyncio.test_tasks"])
shutil.copytree("/opt/poolhouse", work / "tree")
timed("poolhouse_tests", "PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider -n 3 "
      "tests/test_layers.py tests/test_command_group.py", cwd=work / "tree")
EOF
python3 /tmp/bench.py
