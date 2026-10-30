"""Prompt-only skills (package_type=skill, runtime=none) are verified by a file
check, never by pip install / import / smoke.

Regression for the 2026-10 incident: a correct SKILL.md-only skill was run
through `pip install .`, failed with "Neither 'setup.py' nor 'pyproject.toml'
found", scored 0 and was auto-quarantined.
"""

import io
import tarfile
from unittest.mock import MagicMock, patch

from app.verification.pipeline import _run_verification_sync
from app.verification.scoring import compute_score_result

MANIFEST = b"""manifest_version: "0.2"
package_id: demo-skill
package_type: skill
runtime: none
capabilities:
  prompts:
    - name: main
      capability_id: demo_skill.prompt
      template: SKILL.md
"""


def _skill_tarball(files: dict[str, bytes], prefix: str = "demo-skill/") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(prefix + name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _run(artifact: bytes, prompt_templates):
    """Run the sync verifier; any venv or pip call fails the test loudly."""
    with (
        patch(
            "app.verification.sandbox.VerificationSandbox.create_venv",
            side_effect=AssertionError("a prompt-only skill must not create a venv"),
        ) as venv,
        patch(
            "app.verification.sandbox.VerificationSandbox.pip_install",
            side_effect=AssertionError("a prompt-only skill must not be pip-installed"),
        ) as pip,
    ):
        result = _run_verification_sync(artifact, [], prompt_templates=prompt_templates)
    return result, venv, pip


def test_valid_skill_passes_without_pip():
    artifact = _skill_tarball(
        {"agentnode.yaml": MANIFEST, "SKILL.md": b"# Demo\nUse the tool well.\n"}
    )
    result, venv, pip = _run(artifact, ["SKILL.md"])

    assert result["install_status"] == "passed"
    assert result["import_status"] == "passed"
    assert result["smoke_status"] == "skipped"
    assert result["smoke_reason"] == "prompt_only"
    assert result["tests_status"] == "not_present"
    assert result["verification_mode"] == "prompt_only"
    assert result["installer"] == "none"
    assert result["error_summary"] is None
    assert "OK   SKILL.md" in result["install_log"]
    venv.assert_not_called()
    pip.assert_not_called()


def test_skill_without_prefix_directory_passes():
    artifact = _skill_tarball(
        {"agentnode.yaml": MANIFEST, "SKILL.md": b"# Demo\n"}, prefix=""
    )
    result, _, _ = _run(artifact, ["SKILL.md"])
    assert result["install_status"] == "passed"


def test_extra_template_is_checked():
    artifact = _skill_tarball(
        {
            "agentnode.yaml": MANIFEST,
            "SKILL.md": b"# Demo\n",
            "guides/quotes.md": b"Find quotes.\n",
        }
    )
    result, _, _ = _run(artifact, ["SKILL.md", "guides/quotes.md"])
    assert result["install_status"] == "passed"
    assert "OK   guides/quotes.md" in result["install_log"]


def test_missing_skill_md_fails_and_names_the_file():
    artifact = _skill_tarball({"agentnode.yaml": MANIFEST})
    result, _, pip = _run(artifact, ["SKILL.md"])

    assert result["install_status"] == "failed"
    assert result["error_summary"] == "Skill files missing or unreadable"
    assert "FAIL SKILL.md: file not found" in result["install_log"]
    pip.assert_not_called()


def test_missing_declared_template_fails():
    artifact = _skill_tarball({"agentnode.yaml": MANIFEST, "SKILL.md": b"# Demo\n"})
    result, _, _ = _run(artifact, ["SKILL.md", "guides/gone.md"])
    assert result["install_status"] == "failed"
    assert "FAIL guides/gone.md: file not found" in result["install_log"]


def test_empty_skill_md_fails():
    artifact = _skill_tarball({"agentnode.yaml": MANIFEST, "SKILL.md": b"  \n"})
    result, _, _ = _run(artifact, ["SKILL.md"])
    assert result["install_status"] == "failed"
    assert "FAIL SKILL.md: file is empty" in result["install_log"]


def test_non_utf8_skill_md_fails():
    artifact = _skill_tarball(
        {"agentnode.yaml": MANIFEST, "SKILL.md": b"\xff\xfe\x00bad"}
    )
    result, _, _ = _run(artifact, ["SKILL.md"])
    assert result["install_status"] == "failed"
    assert "not valid UTF-8" in result["install_log"]


def test_template_path_escaping_the_package_fails():
    artifact = _skill_tarball({"agentnode.yaml": MANIFEST, "SKILL.md": b"# Demo\n"})
    result, _, _ = _run(artifact, ["SKILL.md", "../../etc/passwd"])
    assert result["install_status"] == "failed"
    assert "path escapes the package" in result["install_log"]


def test_code_packages_still_go_through_pip():
    """Control: without prompt_templates the old install path is unchanged."""
    artifact = _skill_tarball({"agentnode.yaml": MANIFEST, "SKILL.md": b"# Demo\n"})
    with (
        patch(
            "app.verification.sandbox.VerificationSandbox.create_venv",
            return_value=(True, ""),
        ),
        patch(
            "app.verification.sandbox.VerificationSandbox.pip_install",
            return_value=(False, "ERROR: Directory '.' is not installable."),
        ) as pip,
    ):
        result = _run_verification_sync(artifact, [])
    pip.assert_called_once()
    assert result["install_status"] == "failed"
    assert result["error_summary"] == "Package installation failed"


def _prompt_only_vr(**kwargs):
    vr = MagicMock()
    values = {
        "install_status": "passed",
        "import_status": "passed",
        "smoke_status": "skipped",
        "smoke_reason": "prompt_only",
        "tests_status": "not_present",
        "tests_auto_generated": False,
        "reliability": None,
        "determinism_score": None,
        "contract_valid": None,
        "contract_details": None,
        "warnings_count": 0,
        "verification_mode": "prompt_only",
        "stability_log": None,
        "install_duration_ms": 3,
        "smoke_confidence": None,
        "has_explicit_cases": False,
        "is_agent_package": False,
    }
    values.update(kwargs)
    for k, v in values.items():
        setattr(vr, k, v)
    return vr


def test_score_for_a_passing_skill_is_honest():
    result = compute_score_result(_prompt_only_vr())
    breakdown = result.to_dict()["breakdown"]

    assert (
        breakdown["install"]["reason"]
        == "Prompt-only skill: SKILL.md and templates present"
    )
    assert breakdown["import"]["reason"] == "No code to import (prompt-only)"
    assert breakdown["smoke"]["points"] == 0
    assert (
        breakdown["smoke"]["reason"] == "Not applicable: prompt-only skill runs no code"
    )
    assert "failed" not in result.explanation.lower()
    assert result.explanation.startswith("Prompt-only skill:")
    # No code ran, so no runtime credit: never gold, never verified.
    assert result.tier in ("unverified", "partial")


def test_score_for_a_broken_skill_names_the_files():
    result = compute_score_result(
        _prompt_only_vr(install_status="failed", import_status=None, smoke_status=None)
    )
    assert result.score <= 3
    assert "missing or unreadable" in result.explanation
