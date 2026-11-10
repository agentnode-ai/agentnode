"""#42: the public input/output contract of the repository's two GitHub Actions, held as it is today.

Two composite actions live in this repository and both are public by being in a public repository:

* `action/action.yml` -- "AgentNode Publish", installs the DEPRECATED npm CLI (`agentnode-cli`) and
  calls `agentnode validate` / `agentnode publish --json`;
* `publish-action/action.yml` -- also "AgentNode Publish", builds a tarball and posts it with curl.

#31 removes the npm CLI's source and lanes from this repository. The action does not use that
source -- it installs the published npm package -- but it stays on a deprecated path, and any
migration has to keep what a workflow that uses it relies on: the input names, their defaults, the
outputs and how they are filled. This file pins exactly that, so a migration is a deliberate change
to these tests and never a silent one.

What it deliberately does NOT pin is which of the two is THE action, or what the website's
`uses: agentnode/publish@v1` should become: that is the open decision of #42 (see the report),
and `test_the_documented_uses_lines_name_an_action_that_is_in_this_repository` records the state
as found rather than a wish.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]


def _action(rel: str) -> dict:
    return yaml.safe_load((ROOT / rel / "action.yml").read_text(encoding="utf-8"))


# ---- action/ ----------------------------------------------------------------------------------- #


def test_action_inputs_are_the_published_ones():
    inputs = _action("action")["inputs"]
    assert set(inputs) == {"api-key", "manifest", "api-url", "dry-run"}
    assert inputs["api-key"]["required"] is True
    assert inputs["manifest"].get("default") == "" and inputs["manifest"]["required"] is False
    assert inputs["api-url"]["default"] == "https://api.agentnode.net"
    assert inputs["dry-run"]["default"] == "false"


def test_action_outputs_are_the_published_ones():
    outputs = _action("action")["outputs"]
    assert {name: o["value"] for name, o in outputs.items()} == {
        "slug": "${{ steps.publish.outputs.slug }}",
        "version": "${{ steps.publish.outputs.version }}",
        "validation-status": "${{ steps.validate.outputs.status }}",
    }


def test_action_is_composite_and_its_steps_fill_the_outputs():
    runs = _action("action")["runs"]
    assert runs["using"] == "composite"
    ids = {s.get("id") for s in runs["steps"]}
    assert {"detect", "validate", "publish"} <= ids
    text = (ROOT / "action" / "action.yml").read_text(encoding="utf-8")
    for written in ('echo "status=valid"', 'echo "status=invalid"', 'echo "slug=$SLUG"',
                    'echo "version=$VERSION"'):
        assert written in text, written
    # the manifest is found in the order a workflow relies on when it gives none
    assert text.index('"agentnode.yaml"') < text.index('"agentnode.yml"') < text.index('"agentnode.json"')


def test_action_still_installs_the_deprecated_npm_cli():
    """The finding #42 exists for, pinned so that moving off it is a visible, deliberate edit."""
    text = (ROOT / "action" / "action.yml").read_text(encoding="utf-8")
    assert "npm install -g agentnode-cli" in text


# ---- publish-action/ ---------------------------------------------------------------------------- #


def test_publish_action_inputs_are_the_published_ones():
    inputs = _action("publish-action")["inputs"]
    assert set(inputs) == {"api-key", "package-dir", "registry-url"}
    assert inputs["api-key"]["required"] is True
    assert inputs["package-dir"]["default"] == "."
    assert inputs["registry-url"]["default"] == "https://api.agentnode.net"
    assert "outputs" not in _action("publish-action")


# ---- what the documentation tells people to write ----------------------------------------------- #

DOCUMENTED = ["README.md", "docs/publishing.md", "web/src/app/docs/github-action/page.tsx",
              "action/example-workflow.yml", "publish-action/README.md"]


def _uses_lines():
    found = {}
    for rel in DOCUMENTED:
        for ref in re.findall(r"uses:\s*(agentnode[\w./-]*@[\w.-]+)", (ROOT / rel).read_text(encoding="utf-8")):
            found.setdefault(ref, set()).add(rel)
    return found


def test_the_documented_uses_lines_name_an_action_that_is_in_this_repository():
    """As found on 2026-11-10: the README, docs/publishing.md, the website page and
    action/example-workflow.yml tell people to write `uses: agentnode/publish@v1`, a repository
    that does not exist (GitHub answers 404); publish-action/README.md says
    `agentnode-ai/agentnode/publish-action@v1`, and this repository has no tag `v1`. So no
    documented line works today. This test lists them; it fails when a NEW reference appears that
    is not one of these, so the set cannot grow unnoticed while the decision is open."""
    assert _uses_lines() == {
        "agentnode/publish@v1": {"README.md", "docs/publishing.md",
                                 "web/src/app/docs/github-action/page.tsx",
                                 "action/example-workflow.yml"},
        "agentnode-ai/agentnode/publish-action@v1": {"publish-action/README.md"},
    }
