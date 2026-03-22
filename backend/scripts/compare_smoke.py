"""Compare smoke results before/after Phase 1 batch run."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import async_session_factory
from sqlalchemy import text

import app.auth.models
import app.publishers.models
import app.packages.models
import app.verification.models
import app.webhooks.models
import app.shared.models
import app.admin.models
import app.blog.models
import app.sitemap.models

# Pre-batch baseline (from snapshot taken before batch run)
BEFORE = {
    "ai-image-generator-pack": "inconclusive",
    "api-connector-pack": "passed",
    "api-docs-generator-pack": "passed",
    "arxiv-search-pack": "passed",
    "audio-processor-pack": "passed",
    "aws-toolkit-pack": "passed",
    "azure-toolkit-pack": "passed",
    "browser-automation-pack": "passed",
    "calendar-manager-pack": "inconclusive",
    "ci-cd-runner-pack": "passed",
    "citation-manager-pack": "inconclusive",
    "cloud-deploy-pack": "passed",
    "code-executor-pack": "passed",
    "code-linter-pack": "passed",
    "code-refactor-pack": "passed",
    "contract-review-pack": "passed",
    "copywriting-pack": "passed",
    "crm-connector-pack": "inconclusive",
    "csv-analyzer-pack": "passed",
    "database-connector-pack": "passed",
    "data-visualizer-pack": "passed",
    "discord-connector-pack": "passed",
    "docker-manager-pack": "passed",
    "document-redaction-pack": "passed",
    "document-summarizer-pack": "passed",
    "email-automation-pack": "passed",
    "email-drafter-pack": "passed",
    "embedding-generator-pack": None,
    "excel-processor-pack": "passed",
    "file-converter-pack": "passed",
    "gif-creator-pack": "passed",
    "github-integration-pack": "passed",
    "gitlab-connector-pack": "passed",
    "google-workspace-pack": "passed",
    "home-automation-pack": "inconclusive",
    "icon-generator-pack": "passed",
    "image-analyzer-pack": "passed",
    "json-processor-pack": "passed",
    "kubernetes-manager-pack": "passed",
    "markdown-notes-pack": "inconclusive",
    "microsoft-365-pack": "passed",
    "news-aggregator-pack": "passed",
    "notion-connector-pack": "passed",
    "ocr-reader-pack": "passed",
    "pdf-extractor-pack": "passed",
    "pdf-reader-pack": "passed",
    "powerpoint-generator-pack": "inconclusive",
    "project-board-pack": "inconclusive",
    "prompt-engineer-pack": "passed",
    "regex-builder-pack": "passed",
    "research-seo-keywords-and-clusters-them-pack": "passed",
    "scheduler-pack": "inconclusive",
    "scientific-computing-pack": "passed",
    "screenshot-capture-pack": "passed",
    "secret-scanner-pack": "passed",
    "security-audit-pack": "passed",
    "semantic-search-pack": None,
    "seo-optimizer-pack": "passed",
    "slack-connector-pack": None,
    "smart-lights-pack": "inconclusive",
    "social-media-pack": "inconclusive",
    "speech-to-text-pack": None,
    "sql-generator-pack": "passed",
    "task-manager-pack": "inconclusive",
    "telegram-connector-pack": "passed",
    "test-generator-pack": "passed",
    "text-humanizer-pack": "passed",
    "text-to-speech-pack": "passed",
    "text-translator-pack": "passed",
    "user-story-planner-pack": "passed",
    "video-generator-pack": "inconclusive",
    "web-design-pack": "passed",
    "webpage-extractor-pack": "failed",
    "web-search-pack": "passed",
    "whatsapp-connector-pack": "passed",
    "word-counter-pack": "passed",
    "word-document-pack": "inconclusive",
    "youtube-analyzer-pack": "inconclusive",
}


def extract_reason(smoke_log):
    if not smoke_log:
        return None
    for line in smoke_log.splitlines():
        if line.startswith("{"):
            try:
                return json.loads(line).get("reason")
            except Exception:
                pass
    return None


def extract_error_detail(smoke_log):
    if not smoke_log:
        return None
    for line in smoke_log.splitlines():
        if line.startswith("{"):
            try:
                entry = json.loads(line)
                etype = entry.get("error_type", "")
                msg = entry.get("message", "")
                return f"{etype}: {msg[:120]}" if etype else None
            except Exception:
                pass
    return None


async def main():
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT p.slug, vr.smoke_status, vr.smoke_log
            FROM packages p
            JOIN package_versions pv ON p.latest_version_id = pv.id
            LEFT JOIN verification_results vr ON pv.latest_verification_result_id = vr.id
            ORDER BY p.slug
        """))
        rows = r.fetchall()

    regressions_to_failed = []
    passed_to_inconclusive = []
    new_unknown = []
    improved = []

    after_counts = {}
    reason_counts = {}

    for row in rows:
        before = BEFORE.get(row.slug)
        after = row.smoke_status or "null"
        reason = extract_reason(row.smoke_log)
        detail = extract_error_detail(row.smoke_log)

        after_counts[after] = after_counts.get(after, 0) + 1
        if reason:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        if before == "passed" and after == "failed":
            regressions_to_failed.append((row.slug, reason, detail))
        elif before == "passed" and after == "inconclusive":
            passed_to_inconclusive.append((row.slug, reason, detail))
        elif before in (None, "failed", "inconclusive") and after == "passed":
            improved.append((row.slug, before))
        if reason == "unknown_smoke_condition":
            new_unknown.append((row.slug, before, detail))

    sep = "=" * 65

    print(sep)
    print("CHECK 1: passed -> failed REGRESSIONS")
    print(sep)
    if regressions_to_failed:
        for slug, reason, detail in regressions_to_failed:
            print(f"  REGRESSION: {slug} -> {reason}")
            if detail:
                print(f"    {detail}")
    else:
        print("  None!")

    print()
    print(sep)
    print("CHECK 2: passed -> inconclusive (stricter classification)")
    print(sep)
    if passed_to_inconclusive:
        for slug, reason, detail in passed_to_inconclusive:
            print(f"  {slug}: {reason}")
            if detail:
                print(f"    {detail}")
    else:
        print("  None!")

    print()
    print(sep)
    print("CHECK 3: unknown_smoke_condition")
    print(sep)
    print(f"  Total: {len(new_unknown)}")
    for slug, before, detail in new_unknown:
        marker = " (was passed!)" if before == "passed" else ""
        print(f"  {slug}{marker}")
        if detail:
            print(f"    {detail}")

    print()
    print(sep)
    print("IMPROVEMENTS (moved to passed)")
    print(sep)
    if improved:
        for slug, before in improved:
            print(f"  {slug}: {before} -> passed")
    else:
        print("  None")

    print()
    print(sep)
    print("REASON DISTRIBUTION")
    print(sep)
    for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
        print(f"  {reason}: {count}")

    print()
    print(sep)
    print("SUMMARY")
    print(sep)
    print("  Before: 58 passed, 15 inconclusive, 1 failed, 4 null")
    parts = []
    for status in ["passed", "inconclusive", "failed", "null"]:
        if status in after_counts:
            parts.append(f"{after_counts[status]} {status}")
    print(f"  After:  {', '.join(parts)}")


if __name__ == "__main__":
    asyncio.run(main())
