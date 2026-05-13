# Known Backend Test Failures

Last verified: 2026-05-13 (post Phase 5.1 skill boundary hardening)
Result: 27 failed, 981 passed, 8 categories — none related to Phase 5.1

## Auth (1)

| Test | Root cause |
|------|-----------|
| `test_auth.py::test_2fa_setup_and_verify` | 2FA setup returns 422 — likely missing TOTP dependency or config |

## Status Code Mismatches (9)

Tests expect legacy 200, endpoint correctly returns 201 Created.

| Test | Detail |
|------|--------|
| `test_cross_publisher_auth.py::test_cannot_publish_to_other_publishers_package` | Tests expect legacy 200, endpoint correctly returns 201 Created |
| `test_publish_v02.py::test_publish_v02_multi_tool` | Tests expect legacy 200, endpoint correctly returns 201 Created |
| `test_publish_v02.py::test_publish_v02_single_tool` | Tests expect legacy 200, endpoint correctly returns 201 Created |
| `test_publish_v02.py::test_v01_still_publishes` | Tests expect legacy 200, endpoint correctly returns 201 Created |
| `test_publish_v02.py::test_install_info_v02_has_entrypoints` | Cascading from publish step expecting 200 |
| `test_publish_v02.py::test_install_info_v01_null_entrypoints` | Cascading from publish step expecting 200 |
| `test_publish_v02.py::test_install_v02_returns_tools` | Cascading from publish step expecting 200 |
| `test_publish_v02.py::test_install_v01_returns_empty_tools` | Cascading from publish step expecting 200 |
| `test_publish_v02.py::test_publish_compact_v02_with_defaults` | Tests expect legacy 200, endpoint correctly returns 201 Created |

## E2E Flow (3)

| Test | Root cause |
|------|-----------|
| `test_e2e_flow.py::test_full_e2e_flow` | Validate endpoint response changed |
| `test_e2e_flow.py::test_publish_then_deprecate_flow` | Cascading from publish status |
| `test_e2e_flow.py::test_publish_then_yank_version_flow` | Cascading from publish status |

## Install/Download (2)

| Test | Root cause |
|------|-----------|
| `test_install.py::test_get_install_with_artifact` | S3/artifact infrastructure not available in test |
| `test_install.py::test_download_returns_url` | 404 — S3 mock missing |

## Resolution (4)

| Test | Root cause |
|------|-----------|
| `test_resolution.py::test_resolve_single_capability` | MeiliSearch not available in test env |
| `test_resolution.py::test_resolve_multiple_packages_ranked` | MeiliSearch not available |
| `test_resolution.py::test_resolve_framework_filter` | MeiliSearch not available |
| `test_resolution.py::test_resolve_with_limit` | MeiliSearch not available |

## Validator — Summary Length (3)

Tests use fixtures with summaries shorter than 20 chars.

| Test | Error |
|------|-------|
| `test_validator.py::test_valid_manifest` | `summary must be at least 20 characters` |
| `test_validator_v02.py::test_v01_still_valid` | `summary must be at least 20 characters` |
| `test_validator_v02.py::test_accepts_v01` | same |

## Negative Tests — Auth Required (2)

| Test | Root cause |
|------|-----------|
| `test_sprint_i_negative.py::test_validate_rejects_missing_package_id` | Endpoint now requires auth (401) |
| `test_sprint_i_negative.py::test_validate_rejects_non_dict_manifest` | Endpoint now requires auth (401) |

## Verification Scoring (3)

| Test | Root cause |
|------|-----------|
| `test_verification_scoring.py::test_perfect_score` | Scoring formula changed |
| `test_verification_scoring.py::test_tier_gold` | Scoring formula changed |
| `test_verification_scoring.py::test_toolpack_perfect_exact_scores` | Scoring formula changed |
