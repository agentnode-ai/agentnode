"""Tests for prompt-asset discovery in runtime and lockfile."""
import json
import os
import tempfile

import pytest

from agentnode_sdk.models import PromptArgumentSpec, PromptSpec


def _make_lockfile(packages: dict) -> str:
    """Create a temp lockfile and return its path."""
    data = {"lockfile_version": "0.1", "updated_at": "", "packages": packages}
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".lock", delete=False)
    json.dump(data, tf)
    tf.close()
    return tf.name


@pytest.fixture(autouse=True)
def _clean_lockfile_env():
    """Ensure AGENTNODE_LOCKFILE is cleaned up after each test."""
    old = os.environ.get("AGENTNODE_LOCKFILE")
    yield
    if old is not None:
        os.environ["AGENTNODE_LOCKFILE"] = old
    else:
        os.environ.pop("AGENTNODE_LOCKFILE", None)


def _make_runtime():
    """Create a minimal runtime without a real API client."""
    from agentnode_sdk.runtime import AgentNodeRuntime

    rt = AgentNodeRuntime.__new__(AgentNodeRuntime)
    rt._minimum_trust_level = "verified"
    return rt


class TestPromptSpecs:
    def test_returns_prompt_specs_from_lockfile(self):
        path = _make_lockfile({
            "test-pack": {
                "version": "1.0.0",
                "prompts": [{
                    "name": "summarize",
                    "capability_id": "text_summarization",
                    "template": "Summarize: {{text}}",
                    "description": "Summarize text",
                    "arguments": [
                        {"name": "text", "description": "Text input", "required": True},
                    ],
                }],
            },
        })
        os.environ["AGENTNODE_LOCKFILE"] = path
        try:
            rt = _make_runtime()
            specs = rt.prompt_specs()
            assert len(specs) == 1
            assert isinstance(specs[0], PromptSpec)
            assert specs[0].name == "summarize"
            assert specs[0].template == "Summarize: {{text}}"
            assert specs[0].capability_id == "text_summarization"
            assert specs[0].description == "Summarize text"
            assert len(specs[0].arguments) == 1
            assert isinstance(specs[0].arguments[0], PromptArgumentSpec)
            assert specs[0].arguments[0].name == "text"
            assert specs[0].arguments[0].required is True
        finally:
            os.unlink(path)

    def test_empty_prompts_returns_empty_list(self):
        path = _make_lockfile({
            "test-pack": {
                "version": "1.0.0",
                "prompts": [],
            },
        })
        os.environ["AGENTNODE_LOCKFILE"] = path
        try:
            rt = _make_runtime()
            assert rt.prompt_specs() == []
        finally:
            os.unlink(path)

    def test_missing_prompts_field_returns_empty(self):
        """Packages without 'prompts' key (pre-v0.3 lockfile) work fine."""
        path = _make_lockfile({
            "old-pack": {
                "version": "1.0.0",
                "tools": [{"name": "run", "entrypoint": "old_pack.tool:run"}],
            },
        })
        os.environ["AGENTNODE_LOCKFILE"] = path
        try:
            rt = _make_runtime()
            assert rt.prompt_specs() == []
        finally:
            os.unlink(path)

    def test_skips_prompts_without_template(self):
        path = _make_lockfile({
            "test-pack": {
                "version": "1.0.0",
                "prompts": [
                    {"name": "bad", "capability_id": "x"},  # no template
                    {"name": "good", "capability_id": "y", "template": "Hello {{name}}"},
                ],
            },
        })
        os.environ["AGENTNODE_LOCKFILE"] = path
        try:
            rt = _make_runtime()
            specs = rt.prompt_specs()
            assert len(specs) == 1
            assert specs[0].name == "good"
        finally:
            os.unlink(path)

    def test_multiple_packages_merged(self):
        path = _make_lockfile({
            "pack-a": {
                "version": "1.0.0",
                "prompts": [
                    {"name": "prompt_a", "capability_id": "a", "template": "A"},
                ],
            },
            "pack-b": {
                "version": "2.0.0",
                "prompts": [
                    {"name": "prompt_b", "capability_id": "b", "template": "B"},
                ],
            },
        })
        os.environ["AGENTNODE_LOCKFILE"] = path
        try:
            rt = _make_runtime()
            specs = rt.prompt_specs()
            assert len(specs) == 2
            names = {s.name for s in specs}
            assert names == {"prompt_a", "prompt_b"}
        finally:
            os.unlink(path)

    def test_prompt_without_arguments(self):
        path = _make_lockfile({
            "test-pack": {
                "version": "1.0.0",
                "prompts": [{
                    "name": "greet",
                    "capability_id": "greeting",
                    "template": "Hello, world!",
                }],
            },
        })
        os.environ["AGENTNODE_LOCKFILE"] = path
        try:
            rt = _make_runtime()
            specs = rt.prompt_specs()
            assert len(specs) == 1
            assert specs[0].arguments is None
        finally:
            os.unlink(path)


class TestLockfilePromptFields:
    """Verify lockfile correctly stores prompt data from install_package."""

    def test_install_package_stores_prompts_in_lockfile(self):
        from agentnode_sdk.installer import read_lockfile, update_lockfile

        path = _make_lockfile({})
        os.environ["AGENTNODE_LOCKFILE"] = path
        try:
            prompts = [
                {
                    "name": "summarize",
                    "capability_id": "text_summarization",
                    "template": "Summarize: {{text}}",
                    "arguments": [{"name": "text", "required": True}],
                },
            ]
            from pathlib import Path
            update_lockfile("test-pack", {
                "version": "1.0.0",
                "prompts": prompts,
            }, path=Path(path))

            data = read_lockfile(Path(path))
            stored = data["packages"]["test-pack"]["prompts"]
            assert len(stored) == 1
            assert stored[0]["name"] == "summarize"
            assert stored[0]["template"] == "Summarize: {{text}}"
        finally:
            os.unlink(path)
