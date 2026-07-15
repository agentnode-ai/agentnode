"""Shared parsing for package/tool references.

A reference is either a bare package slug (``"word-counter-pack"``) or a
``slug:tool`` pair (``"word-counter-pack:count_words"``). Package slugs are
kebab-case (``[a-z0-9-]``) and never contain ``':'``, so the first ``':'``
unambiguously separates the slug from an optional tool name.

Used by both the CLI run path (``cli.commands.cmd_run``) and the agent runtime
(``runtimes.agent_runner``) so the two stay in lock-step.
"""
from __future__ import annotations

import re

# Central ASCII-kebab package-slug rule — the single source of truth for the slug
# format. Runtime-neutral (this module imports only the stdlib), so both the core
# integrity logic (lock_integrity) and the CLI (cli.init re-exports it as SLUG_RE)
# depend on the same rule without core depending on the CLI. Semantics unchanged.
PACKAGE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")


def is_valid_package_slug(value: object) -> bool:
    """True iff *value* is a string matching the central package-slug rule."""
    return isinstance(value, str) and bool(PACKAGE_SLUG_RE.match(value))


def parse_tool_reference(ref: str) -> tuple[str, str | None]:
    """Parse ``'slug:tool'`` or ``'slug'`` into ``(slug, tool_name)``.

    Returns ``(slug, None)`` when no tool is given (or the tool part is empty,
    e.g. ``"slug:"``), so callers can treat it as "no specific tool".
    """
    slug, _, tool = ref.partition(":")
    return slug, (tool or None)
