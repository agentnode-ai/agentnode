"""Skill loading — read installed skill packages from disk.

Skills are prompt+assets packages with no code execution.
They live in ~/.agentnode/skills/{slug}/ after installation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentnode_sdk.config import config_dir
from agentnode_sdk.installer import read_lockfile


def _skills_dir() -> Path:
    return config_dir() / "skills"


@dataclass(frozen=True)
class SkillAsset:
    id: str
    type: str
    path: Path
    description: str

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.path.read_text(encoding=encoding)

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()


@dataclass
class SkillContext:
    slug: str
    version: str
    prompt_text: str
    prompt_name: str
    assets: list[SkillAsset]
    metadata: dict[str, Any]
    _prompts: dict[str, str] = field(repr=False)
    _install_path: Path = field(repr=False)

    def render(self, **kwargs: str) -> str:
        text = self.prompt_text
        for key, value in kwargs.items():
            text = text.replace("{{" + key + "}}", str(value))
        return text

    def render_prompt(self, name: str, **kwargs: str) -> str:
        if name not in self._prompts:
            raise KeyError(f"Unknown prompt: {name}. Available: {list(self._prompts.keys())}")
        text = self._prompts[name]
        for key, value in kwargs.items():
            text = text.replace("{{" + key + "}}", str(value))
        return text

    @property
    def prompts(self) -> list[str]:
        return list(self._prompts.keys())

    def asset(self, asset_id: str) -> SkillAsset:
        for a in self.assets:
            if a.id == asset_id:
                return a
        raise KeyError(f"Asset not found: {asset_id}. Available: {[a.id for a in self.assets]}")


def load_skill(slug: str) -> SkillContext:
    """Load an installed skill package by slug.

    Reads the skill files from ~/.agentnode/skills/{slug}/ and returns
    a SkillContext with prompt text, assets, and metadata.
    """
    lock = read_lockfile()
    pkg = lock.get("packages", {}).get(slug)
    if not pkg:
        raise ImportError(
            f"Skill '{slug}' is not installed. "
            f"Install it first: agentnode install {slug}"
        )

    if pkg.get("package_type") != "skill":
        raise TypeError(
            f"Package '{slug}' is not a skill (type: {pkg.get('package_type')}). "
            "Use load_tool() for toolpack packages."
        )

    install_path = _skills_dir() / slug
    if not install_path.is_dir():
        raise FileNotFoundError(
            f"Skill directory not found: {install_path}. "
            f"Reinstall with: agentnode install {slug}"
        )

    import yaml

    manifest_path = install_path / "agentnode.yaml"
    if manifest_path.exists():
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            manifest = {}
    else:
        manifest = {}

    manifest_prompts = manifest.get("capabilities", {}).get("prompts", [])
    if not manifest_prompts:
        manifest_prompts = pkg.get("prompts", [])

    prompt_contents: dict[str, str] = {}
    primary_name = ""
    primary_text = ""

    for prompt_def in manifest_prompts:
        if not isinstance(prompt_def, dict):
            continue
        name = prompt_def.get("name", "")
        template = prompt_def.get("template", "")
        if not name or not template:
            continue

        template_path = install_path / template
        if template_path.is_file():
            content = template_path.read_text(encoding="utf-8")
        else:
            content = ""

        prompt_contents[name] = content
        if not primary_name:
            primary_name = name
            primary_text = content

    if not primary_text:
        skill_md = install_path / "SKILL.md"
        if skill_md.is_file():
            primary_text = skill_md.read_text(encoding="utf-8")
            if not primary_name:
                primary_name = slug
            prompt_contents[primary_name] = primary_text

    manifest_assets = manifest.get("assets", [])
    if not manifest_assets:
        manifest_assets = pkg.get("assets", [])

    assets: list[SkillAsset] = []
    for asset_def in manifest_assets:
        if not isinstance(asset_def, dict):
            continue
        asset_id = asset_def.get("id", "")
        asset_type = asset_def.get("type", "document")
        asset_rel_path = asset_def.get("path", "")
        description = asset_def.get("description", "")

        if not asset_id or not asset_rel_path:
            continue

        full_path = install_path / asset_rel_path
        assets.append(SkillAsset(
            id=asset_id,
            type=asset_type,
            path=full_path,
            description=description,
        ))

    return SkillContext(
        slug=slug,
        version=pkg.get("version", ""),
        prompt_text=primary_text,
        prompt_name=primary_name,
        assets=assets,
        metadata=manifest,
        _prompts=prompt_contents,
        _install_path=install_path,
    )
