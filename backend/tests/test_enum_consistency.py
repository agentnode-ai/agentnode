"""Guard: the validator's accepted values must be storable in the DB enums.

This is the regression class behind the skill-publish bug (validator accepted
(skill, none, prompt_only) but the runtime_type / install_mode DB enums lacked
'none' / 'prompt_only', so every real skill publish 500ed at INSERT). These
tests make any drift between what the validator ACCEPTS and what the DB can
STORE fail loudly at test time instead of at a user's publish.

Scope: consistency only — this does not widen any runtime. A value the DB enum
supports but the validator rejects (e.g. typescript/docker/mcp_server) is fine
(intentionally not user-facing yet) and is reported, not failed.
"""

from __future__ import annotations

from app.packages.models import Package, PackageVersion
from app.packages.validator import (
    VALID_INSTALL_MODES,
    VALID_PACKAGE_TYPES,
    VALID_RUNTIMES,
    _VALID_COMBINATIONS,
)


def _db_enum(model, column: str) -> set[str]:
    return set(model.__table__.c[column].type.enums)


DB_PACKAGE_TYPES = _db_enum(Package, "package_type")
DB_RUNTIMES = _db_enum(PackageVersion, "runtime")
DB_INSTALL_MODES = _db_enum(PackageVersion, "install_mode")


def test_validator_package_types_are_storable():
    missing = VALID_PACKAGE_TYPES - DB_PACKAGE_TYPES
    assert not missing, (
        f"validator accepts package_type(s) {missing} the DB enum cannot store — "
        f"publish would 500 at INSERT. DB enum: {sorted(DB_PACKAGE_TYPES)}"
    )


def test_validator_runtimes_are_storable():
    missing = VALID_RUNTIMES - DB_RUNTIMES
    assert not missing, (
        f"validator accepts runtime(s) {missing} the DB enum cannot store. "
        f"DB enum: {sorted(DB_RUNTIMES)}"
    )


def test_validator_install_modes_are_storable():
    missing = VALID_INSTALL_MODES - DB_INSTALL_MODES
    assert not missing, (
        f"validator accepts install_mode(s) {missing} the DB enum cannot store. "
        f"DB enum: {sorted(DB_INSTALL_MODES)}"
    )


def test_every_valid_combination_is_storable():
    """The real bug class: an accepted (type, runtime, install_mode) whose parts
    the DB cannot all store."""
    broken = []
    for pkg_type, runtime, install_mode in _VALID_COMBINATIONS:
        problems = []
        if pkg_type not in DB_PACKAGE_TYPES:
            problems.append(f"package_type={pkg_type!r}")
        if runtime not in DB_RUNTIMES:
            problems.append(f"runtime={runtime!r}")
        if install_mode not in DB_INSTALL_MODES:
            problems.append(f"install_mode={install_mode!r}")
        if problems:
            broken.append(
                f"({pkg_type}, {runtime}, {install_mode}) -> not storable: "
                + ", ".join(problems)
            )
    assert not broken, "validator combos the DB cannot store:\n  " + "\n  ".join(broken)


def test_report_db_only_values_are_not_user_facing():
    """DB enum values the validator does NOT accept are intentional (not yet
    user-facing, e.g. typescript/docker/mcp_server). This test documents them
    and only fails if that assumption is violated by an unexpected new value."""
    known_db_only = {
        "runtime": {"typescript", "docker"},
        "install_mode": {"mcp_server"},
        "package_type": set(),
    }
    assert (DB_RUNTIMES - VALID_RUNTIMES) <= known_db_only["runtime"], (
        f"unexpected DB-only runtime(s): {DB_RUNTIMES - VALID_RUNTIMES - known_db_only['runtime']}"
    )
    assert (DB_INSTALL_MODES - VALID_INSTALL_MODES) <= known_db_only["install_mode"], (
        f"unexpected DB-only install_mode(s): "
        f"{DB_INSTALL_MODES - VALID_INSTALL_MODES - known_db_only['install_mode']}"
    )
    assert (DB_PACKAGE_TYPES - VALID_PACKAGE_TYPES) <= known_db_only["package_type"], (
        f"unexpected DB-only package_type(s): "
        f"{DB_PACKAGE_TYPES - VALID_PACKAGE_TYPES - known_db_only['package_type']}"
    )
