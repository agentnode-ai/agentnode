"""The GitHub Action at action/action.yml (#42 option B, prepared in #60, activation candidate #99).

It replaces the npm-CLI action. What it must keep from the npm action, so a workflow written against it keeps working:
the input names, required flags and defaults, the outputs and the step ids that fill them, the manifest search order
agentnode.yaml -> agentnode.yml -> agentnode.json. That contract is held here as literals (NPM_INPUTS, NPM_OUTPUTS),
taken from action/action.yml at main 22281def. What it adds: `directory`. Underneath: the published Python SDK at ONE
pinned version, the API key only through the environment, and no workflow input expanded inside a shell script.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
NEXT = ROOT / "action" / "action.yml"

#: the public contract of the npm action this one replaces (action/action.yml at main 22281def)
NPM_INPUTS = {
    "api-key": {"required": True, "default": None},
    "manifest": {"required": False, "default": ""},
    "api-url": {"required": False, "default": "https://api.agentnode.net"},
    "dry-run": {"required": False, "default": "false"},
}
NPM_OUTPUTS = {
    "slug": "${{ steps.publish.outputs.slug }}",
    "version": "${{ steps.publish.outputs.version }}",
    "validation-status": "${{ steps.validate.outputs.status }}",
}


def _load(p: Path) -> dict:
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _sdk_version() -> str:
    text = (ROOT / "sdk" / "agentnode_sdk" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def _runs(doc: dict) -> list[str]:
    return [s["run"] for s in doc["runs"]["steps"] if "run" in s]


def test_the_action_is_the_one_github_runs_and_nothing_is_left_beside_it():
    assert NEXT.is_file()
    assert not (ROOT / "action" / "action.yaml").exists()           # GitHub would have to choose
    assert not (ROOT / "action" / "action.next.yml").exists()       # no second, prepared copy left behind


def test_it_keeps_every_input_of_the_npm_action_and_adds_directory():
    new = _load(NEXT)["inputs"]
    assert set(new) == set(NPM_INPUTS) | {"directory"}
    for name, spec in NPM_INPUTS.items():
        assert new[name].get("required") == spec.get("required"), name
        assert new[name].get("default") == spec.get("default"), name
    assert new["directory"]["required"] is False and new["directory"]["default"] == ""


def test_it_keeps_the_outputs_and_the_steps_that_fill_them():
    new = _load(NEXT)
    assert {k: v["value"] for k, v in new["outputs"].items()} == NPM_OUTPUTS
    assert {"detect", "validate", "publish"} <= {s.get("id") for s in new["runs"]["steps"]}


def test_it_installs_the_python_sdk_at_exactly_the_version_this_tree_will_release():
    text = NEXT.read_text(encoding="utf-8")
    pins = re.findall(r"agentnode-sdk==([0-9][^\s\"']*)", text)
    assert pins == [_sdk_version()], pins
    assert "npm" not in text and "agentnode-cli" not in text
    assert "curl" not in text and "yq" not in text and "latest" not in text


def test_its_third_party_steps_are_pinned_like_the_repository_s_own_workflows():
    uses = [s["uses"] for s in _load(NEXT)["runs"]["steps"] if "uses" in s]
    assert uses == ["actions/setup-python@v5"]
    setup = next(s for s in _load(NEXT)["runs"]["steps"] if "uses" in s)
    assert re.fullmatch(r"3\.\d+", str(setup["with"]["python-version"]))


def test_no_input_is_expanded_inside_a_shell_script():
    # ${{ inputs.x }} inside `run:` is pasted into the script before bash parses it (script injection, and a
    # key in the process list); every input reaches the scripts through env instead
    for run in _runs(_load(NEXT)):
        assert "${{ inputs." not in run and "${{ github.event" not in run


def test_the_api_key_reaches_only_the_publishing_step_and_only_through_the_environment():
    steps = _load(NEXT)["runs"]["steps"]
    with_key = [s.get("id") for s in steps if "inputs.api-key" in str(s.get("env", {}))]
    assert with_key == ["publish"]
    publish = next(s for s in steps if s.get("id") == "publish")
    assert publish["env"]["AGENTNODE_API_KEY"] == "${{ inputs.api-key }}"
    assert "--token" not in publish["run"]


def test_it_searches_the_manifest_in_the_order_of_the_npm_action():
    detect = next(s for s in _load(NEXT)["runs"]["steps"] if s.get("id") == "detect")["run"]
    assert detect.index('"agentnode.yaml"') < detect.index('"agentnode.yml"') < detect.index('"agentnode.json"')


def test_it_calls_the_cli_with_the_machine_readable_flags():
    runs = "\n".join(_runs(_load(NEXT)))
    assert re.search(r"agentnode validate .*--manifest .*--json", runs)
    assert re.search(r"agentnode publish .*--manifest .*--json .*--yes", runs)
    assert "grep -o" not in runs          # outputs come from JSON, not from scraping text


def test_dry_run_does_not_publish():
    publish = next(s for s in _load(NEXT)["runs"]["steps"] if s.get("id") == "publish")
    assert publish["if"] == "inputs.dry-run != 'true'"


# ---- the steps' scripts, run the way the runner runs them (`shell: bash` = bash --noprofile --norc -eo pipefail) -- #

import json as _json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

BASH = shutil.which("bash")


def _run_step(step_id: str, tmp_path: Path, cli_stdout: dict, cli_rc: int, extra_env: dict | None = None):
    """Run one step's script under bash -eo pipefail with a stand-in `agentnode` that prints `cli_stdout` as JSON
    and exits `cli_rc`. Returns (exit code, GITHUB_OUTPUT as a dict, output text)."""
    step = next(s for s in _load(NEXT)["runs"]["steps"] if s.get("id") == step_id)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "agentnode"
    fake.write_text("#!/bin/sh\nprintf '%%s' '%s'\nexit %d\n" % (_json.dumps(cli_stdout), cli_rc), encoding="utf-8",
                    newline="\n")
    fake.chmod(0o755)
    pydir = Path(sys.executable).parent                            # the steps call `python` to read the JSON
    script = tmp_path / "step.sh"
    script.write_text(step["run"], encoding="utf-8", newline="\n")
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    env = dict(os.environ, GITHUB_OUTPUT=str(out).replace("\\", "/"), MANIFEST="agentnode.yaml", PKG_DIR=".",
               PATH=os.pathsep.join([str(bindir), str(pydir), os.environ.get("PATH", "")]))
    env.update(extra_env or {})
    if not (pydir / "python").exists() and not (pydir / "python.exe").exists():
        pytest.skip("no `python` next to the test interpreter")
    r = subprocess.run([BASH, "--noprofile", "--norc", "-eo", "pipefail", str(script)], cwd=tmp_path, env=env,
                       capture_output=True, text=True)
    got = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if "=" in line)
    return r.returncode, got, r.stdout + r.stderr


@pytest.mark.skipif(BASH is None, reason="bash is needed to run the step the way the runner does")
def test_a_failed_validation_still_reports_invalid_under_the_runners_errexit(tmp_path):
    rc, got, text = _run_step("validate", tmp_path, {"status": "invalid", "errors": ["version"]}, 1)
    assert rc != 0
    assert got.get("status") == "invalid", text


@pytest.mark.skipif(BASH is None, reason="bash is needed to run the step the way the runner does")
def test_a_passed_validation_reports_valid(tmp_path):
    rc, got, text = _run_step("validate", tmp_path, {"status": "valid", "errors": []}, 0)
    assert rc == 0 and got.get("status") == "valid", text


@pytest.mark.skipif(BASH is None, reason="bash is needed to run the step the way the runner does")
def test_a_publish_fills_slug_and_version_from_the_json(tmp_path):
    rc, got, text = _run_step("publish", tmp_path, {"status": "published", "slug": "s-pack", "version": "1.0.0"}, 0,
                              {"AGENTNODE_API_KEY": "k", "AGENTNODE_API_URL": "http://127.0.0.1:9"})
    assert rc == 0 and got == {"slug": "s-pack", "version": "1.0.0"}, text


@pytest.mark.skipif(BASH is None, reason="bash is needed to run the step the way the runner does")
def test_a_failed_publish_fails_the_step_without_outputs(tmp_path):
    rc, got, text = _run_step("publish", tmp_path, {"status": "error", "http_status": 409}, 1,
                              {"AGENTNODE_API_KEY": "k", "AGENTNODE_API_URL": "http://127.0.0.1:9"})
    assert rc != 0 and got == {} and "::error::Publish failed" in text


# ---- how consumers reach it (Codex R-AB-0001: the documented reference did not exist) -------------------------------

#: GitHub resolves `uses: OWNER/REPO/PATH@REF` to PATH/action.yml at REF. This repository is agentnode-ai/agentnode
#: and the action lives in action/. The tag is `action-v1` (cli-v* / sdk-v* pattern; a bare `v1` would read as SDK 1.0
#: next to the v0.x.y SDK tags). It is created only at activation (AKTIVIERUNG-99.md).
CANONICAL_USES = "agentnode-ai/agentnode/action@action-v1"


def test_the_example_workflow_uses_the_canonical_reference():
    example = _load(ROOT / "action" / "example-workflow.yml")
    uses = [s["uses"] for job in example["jobs"].values() for s in job["steps"] if "uses" in s]
    ours = [u for u in uses if not u.startswith("actions/")]
    assert ours == [CANONICAL_USES], ours


def test_the_canonical_reference_resolves_to_this_action():
    owner_repo_path, _sep, _ref = CANONICAL_USES.partition("@")
    owner, repo, path = owner_repo_path.split("/", 2)
    assert (owner, repo) == ("agentnode-ai", "agentnode")
    assert (ROOT / path / "action.yml").resolve() == NEXT.resolve()


def test_the_example_job_asks_for_no_more_than_reading_the_repository():
    example = _load(ROOT / "action" / "example-workflow.yml")
    for job in example["jobs"].values():
        assert job.get("permissions") == {"contents": "read"}
