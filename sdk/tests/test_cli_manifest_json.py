"""#60 (option B of #42): `agentnode validate` / `agentnode publish` gain `--json` and `--manifest <file>`.

Additive only (CLI V1 rule): a call without the new flags behaves exactly as before; that is held by the
golden comparison of the existing calls (pv T-0237) and by the unchanged tests in test_publish.py /
test_validate*.py. What is new:

* `--json`: stdout carries exactly ONE JSON document (the human text goes to stderr), so a CI step can read
  `slug`, `version` and `status` without parsing prose. The exit codes stay what they are.
* `--manifest <file>`: the manifest may be `.yaml`, `.yml` or `.json`, anywhere; the package directory is the
  positional path as before. The registry looks for `agentnode.yaml` inside the artifact (skill verification
  needs it, the skill file allow-list only admits it, the toolpack cassette check reads it), so a manifest
  given this way travels in the artifact AS `agentnode.yaml`, and the original file is not packed twice.
"""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

yaml = pytest.importorskip("yaml")

from agentnode_sdk.cli import publish as P  # noqa: E402
from agentnode_sdk.cli.commands import cmd_validate  # noqa: E402
from agentnode_sdk.cli.main import main as cli_main  # noqa: E402
from agentnode_sdk.cli.validate import validate_package_dir  # noqa: E402

TOOLPACK = {
    "package_id": "json-pack",
    "version": "1.2.3",
    "name": "JSON Pack",
    "package_type": "toolpack",
    "summary": "A package for the machine-readable CLI output tests.",
    "capabilities": {"tools": [{"name": "t", "capability_id": "json.t"}]},
    "verification": {"cases": [{"name": "case-one", "input": {"q": "x"}, "expected": {"status": "ok"}}]},
}
SKILL = {
    "package_id": "json-skill",
    "version": "0.1.0",
    "package_type": "skill",
    "summary": "A skill for the machine-readable CLI output tests.",
    "runtime": "none",
    "install_mode": "prompt_only",
    "capabilities": {"prompts": [{"name": "p", "description": "d", "template": "SKILL.md"}]},
    "permissions": {"network": {"level": "none"}, "filesystem": {"level": "none"},
                    "code_execution": {"level": "none"}, "data_access": {"level": "input_only"},
                    "user_approval": {"required": "never"}},
}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("AGENTNODE_API_KEY", raising=False)
    monkeypatch.setenv("AGENTNODE_NON_INTERACTIVE", "1")


def _pkg(tmp_path: Path, manifest: dict, name: str = "agentnode.yaml", folder: str = "pkg") -> Path:
    d = tmp_path / folder
    (d / "tests").mkdir(parents=True, exist_ok=True)
    (d / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    if manifest.get("package_type") == "skill":
        (d / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    if name:
        text = json.dumps(manifest, indent=2) if name.endswith(".json") else yaml.safe_dump(manifest, sort_keys=False)
        (d / name).write_text(text, encoding="utf-8")
    return d


def _one_json(out: str) -> dict:
    doc = json.loads(out)          # the WHOLE of stdout must be one JSON document
    assert isinstance(doc, dict)
    return doc


def _tar(artifact: bytes) -> dict:
    with tarfile.open(fileobj=io.BytesIO(artifact), mode="r:gz") as t:
        return {m.name: (t.extractfile(m).read() if m.isfile() else None) for m in t.getmembers()}


# ---- validate --json ------------------------------------------------------------------------- #


def test_validate_json_valid_package(tmp_path, capsys):
    d = _pkg(tmp_path, TOOLPACK)
    rc = cmd_validate(str(d), json_output=True)
    doc = _one_json(capsys.readouterr().out)
    assert rc == 0
    assert doc["status"] == "valid" and doc["package_id"] == "json-pack" and doc["version"] == "1.2.3"
    assert doc["package_type"] == "toolpack" and doc["errors"] == []
    assert all({"label", "passed", "detail"} <= set(c) for c in doc["checks"])


def test_validate_json_invalid_package_keeps_exit_code_1(tmp_path, capsys):
    d = _pkg(tmp_path, dict(TOOLPACK, version="one"))
    rc = cmd_validate(str(d), json_output=True)
    doc = _one_json(capsys.readouterr().out)
    assert rc == 1 and doc["status"] == "invalid" and "version" in doc["errors"]


def test_validate_json_without_any_manifest(tmp_path, capsys):
    d = tmp_path / "empty"
    d.mkdir()
    rc = cmd_validate(str(d), json_output=True)
    doc = _one_json(capsys.readouterr().out)
    assert rc == 1 and doc["status"] == "invalid"


def test_validate_human_output_unchanged_without_the_flag(tmp_path, capsys):
    d = _pkg(tmp_path, TOOLPACK)
    assert cmd_validate(str(d)) == 0
    out = capsys.readouterr().out
    assert "AgentNode Package Validation" in out and not out.lstrip().startswith("{")


# ---- --manifest: .yaml / .yml / .json, anywhere ------------------------------------------------ #


@pytest.mark.parametrize("name", ["agentnode.yml", "agentnode.json", "manifests/release.yaml"])
def test_validate_manifest_in_any_format_gives_the_same_result_as_agentnode_yaml(tmp_path, capsys, name):
    ref = _pkg(tmp_path, TOOLPACK, folder="ref")
    other = _pkg(tmp_path, TOOLPACK, name="", folder="other")
    target = other / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(TOOLPACK) if name.endswith(".json") else yaml.safe_dump(TOOLPACK), encoding="utf-8")
    a = validate_package_dir(ref)
    b = validate_package_dir(other, manifest_path=target)
    assert (a.package_id, a.version, a.package_type, a.max_tier) == (b.package_id, b.version, b.package_type, b.max_tier)
    assert [(c.passed, c.detail) for c in a.checks[1:]] == [(c.passed, c.detail) for c in b.checks[1:]]
    assert b.checks[0].passed and not b.has_errors


def test_validate_manifest_outside_the_package_directory(tmp_path, capsys):
    d = _pkg(tmp_path, TOOLPACK, name="")
    m = tmp_path / "elsewhere" / "pack.json"
    m.parent.mkdir()
    m.write_text(json.dumps(TOOLPACK), encoding="utf-8")
    assert cmd_validate(str(d), manifest=str(m), json_output=True) == 0
    doc = _one_json(capsys.readouterr().out)
    assert doc["status"] == "valid" and doc["manifest"] == str(m)


def test_validate_manifest_that_does_not_exist(tmp_path, capsys):
    d = _pkg(tmp_path, TOOLPACK)
    assert cmd_validate(str(d), manifest=str(d / "nope.yml"), json_output=True) == 1
    assert _one_json(capsys.readouterr().out)["status"] == "invalid"


def test_validate_manifest_with_an_unsupported_extension(tmp_path, capsys):
    d = _pkg(tmp_path, TOOLPACK)
    (d / "agentnode.toml").write_text("package_id = 'x'\n", encoding="utf-8")
    assert cmd_validate(str(d), manifest=str(d / "agentnode.toml"), json_output=True) == 1
    doc = _one_json(capsys.readouterr().out)
    assert doc["status"] == "invalid" and any(".toml" in c["detail"] for c in doc["checks"])


def test_validate_json_manifest_that_is_not_an_object(tmp_path, capsys):
    d = _pkg(tmp_path, TOOLPACK, name="")
    (d / "agentnode.json").write_text("[1, 2]", encoding="utf-8")
    assert cmd_validate(str(d), manifest=str(d / "agentnode.json"), json_output=True) == 1


# ---- publish --json ------------------------------------------------------------------------- #


def test_publish_dry_run_json(tmp_path, capsys):
    d = _pkg(tmp_path, TOOLPACK)
    rc = P.cmd_publish(str(d), dry_run=True, json_output=True)
    doc = _one_json(capsys.readouterr().out)
    assert rc == 0 and doc["status"] == "dry_run" and doc["slug"] == "json-pack" and doc["version"] == "1.2.3"


def test_publish_json_success(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("AGENTNODE_API_KEY", "ank_test_secret_value")
    d = _pkg(tmp_path, TOOLPACK)
    resp = {"slug": "json-pack", "version": "1.2.3", "quarantined": False, "warnings": []}
    with patch("agentnode_sdk.cli.publish._post_publish", return_value=resp):
        rc = P.cmd_publish(str(d), yes=True, json_output=True)
    captured = capsys.readouterr()
    doc = _one_json(captured.out)
    assert rc == 0
    assert doc == {"command": "publish", "status": "published", "slug": "json-pack", "version": "1.2.3",
                   "package_id": "json-pack", "quarantined": False, "warnings": []}
    assert "ank_test_secret_value" not in captured.out + captured.err


def test_publish_json_quarantined(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("AGENTNODE_API_KEY", "k")
    d = _pkg(tmp_path, TOOLPACK)
    resp = {"slug": "json-pack", "version": "1.2.3", "quarantined": True, "quarantine_reason": "first publish",
            "warnings": []}
    with patch("agentnode_sdk.cli.publish._post_publish", return_value=resp):
        assert P.cmd_publish(str(d), yes=True, json_output=True) == 0
    doc = _one_json(capsys.readouterr().out)
    assert doc["status"] == "published" and doc["quarantined"] is True


def test_publish_json_registry_error_keeps_exit_code_1(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("AGENTNODE_API_KEY", "k")
    d = _pkg(tmp_path, TOOLPACK)
    err = {"_error": True, "status": 409, "code": "VERSION_EXISTS", "message": "exists", "details": []}
    with patch("agentnode_sdk.cli.publish._post_publish", return_value=err):
        assert P.cmd_publish(str(d), yes=True, json_output=True) == 1
    doc = _one_json(capsys.readouterr().out)
    assert doc["status"] == "error" and doc["http_status"] == 409 and doc["code"] == "VERSION_EXISTS"


def test_publish_json_without_yes_is_refused_not_sent(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("AGENTNODE_API_KEY", "k")
    d = _pkg(tmp_path, TOOLPACK)
    with patch("agentnode_sdk.cli.publish._post_publish") as post:
        assert P.cmd_publish(str(d), json_output=True) == 1
    post.assert_not_called()
    assert _one_json(capsys.readouterr().out)["status"] == "refused"


def test_publish_json_validation_failure(tmp_path, capsys):
    d = _pkg(tmp_path, dict(TOOLPACK, version="one"))
    assert P.cmd_publish(str(d), dry_run=True, json_output=True) == 1
    doc = _one_json(capsys.readouterr().out)
    assert doc["status"] == "invalid" and "version" in doc["errors"]


# ---- publish --manifest: the artifact carries it as agentnode.yaml ---------------------------- #


def _published_artifact(d: Path, manifest: str, monkeypatch) -> tuple[dict, dict]:
    monkeypatch.setenv("AGENTNODE_API_KEY", "k")
    seen = {}

    def fake_post(manifest_json, artifact_bytes, api_key):
        seen["manifest"] = json.loads(manifest_json)
        seen["artifact"] = artifact_bytes
        return {"slug": seen["manifest"]["package_id"], "version": seen["manifest"]["version"]}

    with patch("agentnode_sdk.cli.publish._post_publish", side_effect=fake_post):
        assert P.cmd_publish(str(d), yes=True, json_output=True, manifest=manifest) == 0
    return seen["manifest"], _tar(seen["artifact"])


@pytest.mark.parametrize("name", ["agentnode.yml", "agentnode.json"])
def test_publish_manifest_travels_as_agentnode_yaml(tmp_path, monkeypatch, capsys, name):
    d = _pkg(tmp_path, TOOLPACK, name=name)
    sent, files = _published_artifact(d, str(d / name), monkeypatch)
    assert sent["package_id"] == "json-pack"
    assert "json-pack/agentnode.yaml" in files, "no agentnode.yaml in the artifact"
    assert ("json-pack/%s" % name) not in files, "manifest packed twice: %s" % name
    assert yaml.safe_load(files["json-pack/agentnode.yaml"]) == TOOLPACK


def test_publish_yml_manifest_keeps_its_bytes_in_the_artifact(tmp_path, monkeypatch, capsys):
    d = _pkg(tmp_path, TOOLPACK, name="")
    raw = "# a comment the publisher wrote\n" + yaml.safe_dump(TOOLPACK)
    (d / "agentnode.yml").write_text(raw, encoding="utf-8")
    _, files = _published_artifact(d, str(d / "agentnode.yml"), monkeypatch)
    assert files["json-pack/agentnode.yaml"] == (d / "agentnode.yml").read_bytes()
    assert files["json-pack/agentnode.yaml"].decode("utf-8").startswith("# a comment the publisher wrote")


def test_publish_skill_with_a_yml_manifest_packs_only_what_a_skill_may_contain(tmp_path, monkeypatch, capsys):
    d = _pkg(tmp_path, SKILL, name="agentnode.yml")
    _, files = _published_artifact(d, str(d / "agentnode.yml"), monkeypatch)
    packed = {n for n, data in files.items() if data is not None}
    assert packed == {"json-skill/agentnode.yaml", "json-skill/SKILL.md"}


def test_publish_manifest_outside_the_package(tmp_path, monkeypatch, capsys):
    d = _pkg(tmp_path, TOOLPACK, name="")
    m = tmp_path / "ci" / "pack.json"
    m.parent.mkdir()
    m.write_text(json.dumps(TOOLPACK), encoding="utf-8")
    _, files = _published_artifact(d, str(m), monkeypatch)
    assert yaml.safe_load(files["json-pack/agentnode.yaml"]) == TOOLPACK


def test_publish_refuses_a_second_manifest_beside_agentnode_yaml(tmp_path, capsys):
    # two manifests for one package: which one the registry would see is not obvious, so it is refused
    d = _pkg(tmp_path, TOOLPACK)
    (d / "agentnode.json").write_text(json.dumps(dict(TOOLPACK, version="9.9.9")), encoding="utf-8")
    assert P.cmd_publish(str(d), dry_run=True, json_output=True, manifest=str(d / "agentnode.json")) == 1
    doc = _one_json(capsys.readouterr().out)
    assert doc["status"] == "error" and "agentnode.yaml" in doc["message"]


def test_publish_with_its_own_agentnode_yaml_as_manifest_builds_the_same_artifact(tmp_path):
    d = _pkg(tmp_path, TOOLPACK)
    plain, n1 = P._build_artifact(d, "json-pack")
    given, n2 = P._build_artifact(d, "json-pack", manifest_path=d / "agentnode.yaml")
    assert n1 == n2 and _tar(plain) == _tar(given)


# ---- the command line itself ----------------------------------------------------------------- #


def test_the_flags_reach_the_commands(tmp_path, capsys, monkeypatch):
    d = _pkg(tmp_path, TOOLPACK, name="agentnode.json")
    assert cli_main(["validate", str(d), "--manifest", str(d / "agentnode.json"), "--json"]) == 0
    assert _one_json(capsys.readouterr().out)["status"] == "valid"
    assert cli_main(["publish", str(d), "--manifest", str(d / "agentnode.json"), "--dry-run", "--json"]) == 0
    assert _one_json(capsys.readouterr().out)["status"] == "dry_run"
