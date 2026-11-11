"""Task #20: docs/sandbox/availability.md comes from a working generator and says what is there.

The matrix is generated: ``_checks/extract_facts.py`` reads the SDK source into
``_facts/code-facts.json``, ``_checks/build_matrix.py`` turns facts plus recorded test evidence
(``_facts/test-evidence.json``) into ``availability.md``, and ``_checks/check_docs.py`` regenerates
both and fails on any difference. Found on 2026-11-06:

* the generator refused ("'The EM-3 selection contract' is 'planned', but ... the code has it"),
  so the page had been frozen since 2026-09-04 while the code moved on;
* the self-hosted gateway (``agentnode gateway`` / ``agentnode remote``) was "planned" because the
  only fact behind that row asked for a remote *SandboxBackend* class -- which is a different thing;
* on Windows the writers produced CRLF and backslash paths in unsorted order, so the drift check
  could never pass there.

Every run here happens in a temporary COPY of the directories the scripts read, so nothing in the
repository is written by a test. Statuses may not claim more than the evidence carries: no new
"tested" row without a recorded run in ``_facts/test-evidence.json``.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SANDBOX_DOCS = ROOT / "docs" / "sandbox"



def _tree(tmp: Path) -> Path:
    """A copy of exactly what the three scripts read; returns its docs/sandbox."""
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(SANDBOX_DOCS, tmp / "docs" / "sandbox", ignore=ignore)
    if (ROOT / "docs" / "blog" / "drafts").exists():
        shutil.copytree(ROOT / "docs" / "blog" / "drafts", tmp / "docs" / "blog" / "drafts")
    shutil.copytree(ROOT / "sdk" / "agentnode_sdk", tmp / "sdk" / "agentnode_sdk", ignore=ignore)
    shutil.copytree(ROOT / "sdk" / "docs", tmp / "sdk" / "docs", ignore=ignore)
    if (ROOT / "sdk" / "tests" / "conformance").exists():
        (tmp / "sdk" / "tests" / "conformance").mkdir(parents=True)
    return tmp / "docs" / "sandbox"


def _run(docs: Path, script: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(docs / "_checks" / script)], capture_output=True,
                          text=True, cwd=str(docs), timeout=300)


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    docs = _tree(tmp_path_factory.mktemp("gen"))
    extract, build = _run(docs, "extract_facts.py"), _run(docs, "build_matrix.py")
    return docs, extract, build


def _row(docs: Path, name: str) -> str:
    for line in (docs / "availability.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("| **%s** |" % name):
            return line
    raise AssertionError("no row %r in availability.md" % name)


def test_the_generator_runs_on_the_current_code(generated):
    _docs, extract, build = generated
    assert extract.returncode == 0, extract.stdout + extract.stderr
    assert build.returncode == 0, build.stdout + build.stderr


def test_the_facts_are_the_same_on_every_platform(generated):
    docs, *_ = generated
    raw = (docs / "_facts" / "code-facts.json").read_bytes()
    assert b"\r" not in raw, "CRLF from the writer: the drift check cannot pass on Windows"
    importers = json.loads(raw)["wired_in"]["em3_contract_importers"]
    assert all("\\" not in p for p in importers), importers
    assert importers == sorted(importers), importers
    assert b"\r" not in (docs / "availability.md").read_bytes()


def test_the_self_hosted_gateway_row_says_what_is_there(generated):
    docs, *_ = generated
    row = _row(docs, "Your own sandbox server")
    assert "planned" not in row.split("|")[2], row
    assert "available, not tested here" in row, row       # code exists; no recorded run carries it
    assert "agentnode gateway" in row and "agentnode remote" in row, row
    assert "self-hosted-sandbox.md" in row, row
    assert "agentnode run" in row, row                    # what it still is not: a run_tool backend
    facts = json.loads((docs / "_facts" / "code-facts.json").read_text(encoding="utf-8"))
    assert facts["wired_in"]["self_hosted_gateway_commands"], facts["wired_in"]
    assert facts["wired_in"]["remote_backend_exists"] is False


def test_the_em3_contract_row_is_no_longer_planned(generated):
    docs, *_ = generated
    row = _row(docs, "The EM-3 selection contract")
    assert "planned" not in row.split("|")[2], row
    assert "available, not tested here" in row, row


def test_no_new_row_claims_tested_without_a_recorded_run(generated):
    """The tested rows are exactly the ones test-evidence.json carries -- none was added."""
    docs, *_ = generated
    text = (docs / "availability.md").read_text(encoding="utf-8")
    tested = [ln.split("|")[1].strip() for ln in text.splitlines()
              if ln.startswith("| **") and "✅ available, tested" in ln]
    assert tested == ["**Linux PC**", "**Tool packs run in a container**",
                      "**MCP servers run in a container**",
                      "**Community agents run in a container**",
                      "**Refusal when no runtime exists**",
                      "**A sandboxed program reaching only the sites its install sealed**"], tested


def test_a_second_run_changes_nothing(generated):
    docs, *_ = generated
    before = {p: (docs / p).read_bytes() for p in ("_facts/code-facts.json", "availability.md")}
    assert _run(docs, "extract_facts.py").returncode == 0
    assert _run(docs, "build_matrix.py").returncode == 0
    after = {p: (docs / p).read_bytes() for p in before}
    assert after == before


def test_the_committed_files_are_what_the_generator_writes(generated):
    docs, *_ = generated
    for rel in ("_facts/code-facts.json", "availability.md"):
        assert (SANDBOX_DOCS / rel).read_bytes() == (docs / rel).read_bytes(), (
            "%s in the repository is not what the generator writes from the current code" % rel)


def test_the_documentation_check_passes(tmp_path):
    docs = _tree(tmp_path)
    check = _run(docs, "check_docs.py")
    assert check.returncode == 0, check.stdout + check.stderr
