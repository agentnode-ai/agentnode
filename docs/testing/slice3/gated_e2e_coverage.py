"""Slice 3 (#34): which E2E-gated tests (skipped without the flags) does any workflow run AT HEAD with the flag set?

usage:  python docs/testing/slice3/gated_e2e_coverage.py <log>

Input: the -rs output of the gated files run WITHOUT the E2E flags (the command is in INVENTORY.md). For each skip that is about a container runtime
or an E2E flag (not POSIX-only), name its class/function and say which workflow runs it at the current
head with the flag, from the workflow text itself.
"""
import ast
import pathlib
import re
import sys

SDK = pathlib.Path(__file__).resolve().parents[3] / "sdk"
WF = SDK.parent / ".github" / "workflows"
LOG = pathlib.Path(sys.argv[1])

workflows = {p.name: p.read_text(encoding="utf-8") for p in WF.glob("*.yml")}


def enclosing(path, line):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls, fn = None, None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.lineno <= line <= node.end_lineno:
            if cls is None or node.lineno > cls.lineno:
                cls = node
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno - 12 <= line <= node.end_lineno:
            if fn is None or node.lineno > fn.lineno:
                fn = node
    return (cls.name if cls else ""), (fn.name if fn else "")


def runners(fname, cls, fn):
    """Workflows (other than sdk-lanes, which runs a frozen commit) that run this test with an E2E flag."""
    out = []
    for name, text in workflows.items():
        if name == "sdk-lanes.yml" or "_E2E: \"1\"" not in text:
            continue
        for m in re.finditer(r"tests/%s\.py(::[A-Za-z_]+)?(::[A-Za-z_]+)?" % fname, text):
            sel = [s.lstrip(":") for s in (m.group(1), m.group(2)) if s]
            if not sel or sel[0] in (cls, fn):
                # the flag must be set in the same step: look back from the match for the step's env
                step = text[:m.start()].rsplit("- name:", 1)[-1]
                if "_E2E: \"1\"" in step:
                    out.append(name)
    return sorted(set(out))


rows, gaps, covered = [], 0, 0
for line in LOG.read_text(encoding="utf-8").splitlines():
    m = re.match(r"SKIPPED \[(\d+)\] tests[\\/](\w+)\.py:(\d+): (.*)", line)
    if not m or "POSIX" in m.group(4):
        continue
    n, fname, ln, why = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)
    cls, fn = enclosing(SDK / "tests" / (fname + ".py"), ln)
    who = runners(fname, cls, fn)
    if who:
        covered += n
    else:
        gaps += n
    rows.append("%-30s %3d  %-44s %-40s %s" % (fname, n, cls or "-", fn or "-",
                                             ", ".join(who) if who else "NOWHERE at head (sdk-lanes: frozen 2de83e79 only)"))
print("%-30s %3s  %-44s %-40s %s" % ("file", "n", "class", "function", "runs with the flag at head in"))
print("\n".join(rows))
print("\ngated tests run at head elsewhere: %d   run nowhere at head: %d" % (covered, gaps))
