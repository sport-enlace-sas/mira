"""Tests for immutable per-SHA review reports."""

from __future__ import annotations

import sqlite3

from mira.core.review_reports import (
    ImpactMagnitude,
    ImpactReport,
    analyze_pr_impact,
    build_compact_audit_report,
    build_evolution_report,
    make_report_marker,
)
from mira.index.store import IndexStore
from mira.models import (
    FileChangeType,
    OverlapFinding,
    ReviewComment,
    Severity,
    ThreadDecision,
    WalkthroughConfidenceScore,
    WalkthroughFileEntry,
    WalkthroughResult,
)


def _comment(path: str = "src/auth.py", title: str = "Unsafe token") -> ReviewComment:
    return ReviewComment(
        path=path,
        line=12,
        end_line=None,
        severity=Severity.WARNING,
        category="security",
        title=title,
        body="The token is trusted without validation.",
        confidence=0.94,
    )


def test_report_marker_is_scoped_to_kind_and_full_sha():
    assert make_report_marker("walkthrough", "ABCDEF123") == (
        "<!-- mira-report:walkthrough:abcdef123 -->"
    )


def test_evolution_report_marks_first_audit_and_new_findings():
    markdown = build_evolution_report(
        head_sha="a" * 40,
        previous_sha="",
        decisions=[],
        new_findings=[_comment()],
        unverifiable_threads=[],
    )

    assert make_report_marker("evolution", "a" * 40) in markdown
    assert "First recorded audit" in markdown
    assert "**New:** 1" in markdown
    assert "Regressed" not in markdown
    assert "`src/auth.py:12` — Unsafe token" in markdown


def test_evolution_report_keeps_resolution_reasons_distinct():
    decisions = [
        ThreadDecision(
            thread_id="fixed",
            path="src/fixed.py",
            line=1,
            body="Fixed finding",
            fixed=True,
            status="fixed_by_code",
            evidence="validation added",
        ),
        ThreadDecision(
            thread_id="present",
            path="src/present.py",
            line=2,
            body="Still present finding",
            fixed=False,
            status="still_present",
            evidence="unsafe call remains",
        ),
        ThreadDecision(
            thread_id="false-positive",
            path="src/safe.py",
            line=3,
            body="Incorrect finding",
            fixed=True,
            status="rejected_false_positive",
            evidence="guard already existed",
        ),
    ]
    unverifiable = [
        ThreadDecision(
            thread_id="unknown",
            path="src/unknown.py",
            line=4,
            body="Could not verify",
            fixed=False,
            status="unverifiable",
            evidence="file could not be loaded",
        )
    ]

    markdown = build_evolution_report(
        head_sha="b" * 40,
        previous_sha="a" * 40,
        decisions=decisions,
        new_findings=[_comment("src/new.py", "New finding")],
        unverifiable_threads=unverifiable,
    )

    assert "**Fixed by code:** 1" in markdown
    assert "**Still present:** 1" in markdown
    assert "**Rejected false positive:** 1" in markdown
    assert "**Unverifiable:** 1" in markdown
    assert "validation added" in markdown
    assert "guard already existed" in markdown


def test_evolution_report_recognizes_regression_without_using_line_number():
    historical = [
        ThreadDecision(
            thread_id="old",
            path="src/auth.py",
            line=9,
            body="**Unsafe token**",
            fixed=False,
            status="resolved_before_audit",
            evidence="Thread was already resolved.",
        ),
        ThreadDecision(
            thread_id="other",
            path="src/other.py",
            line=1,
            body="**Old finding**",
            fixed=False,
            status="resolved_before_audit",
            evidence="Thread was already resolved.",
        ),
    ]

    markdown = build_evolution_report(
        head_sha="c" * 40,
        previous_sha="b" * 40,
        decisions=[],
        new_findings=[_comment(path="src/auth.py", title="Unsafe token")],
        unverifiable_threads=[],
        historical_resolved_threads=historical,
    )

    assert "**Regressed:** 1" in markdown
    assert "**Resolved before this audit:** 1" in markdown
    assert "**New:**" not in markdown


def test_impact_report_detects_next_route_nest_endpoint_and_before_after():
    diff = """diff --git a/apps/web/app/(account)/settings/page.tsx b/apps/web/app/(account)/settings/page.tsx
--- a/apps/web/app/(account)/settings/page.tsx
+++ b/apps/web/app/(account)/settings/page.tsx
@@ -1,1 +1,1 @@
-return <OldSettings />
+return <NewSettings />
diff --git a/apps/api/src/users.controller.ts b/apps/api/src/users.controller.ts
--- a/apps/api/src/users.controller.ts
+++ b/apps/api/src/users.controller.ts
@@ -1,1 +1,3 @@
 @Controller('users')
+@Get(':id')
+findOne() {}
"""

    report = analyze_pr_impact(diff, head_sha="c" * 40)
    markdown = report.to_markdown()

    assert report.magnitude is ImpactMagnitude.MODERATE
    assert "/settings" in report.visual_routes
    assert "GET /users/:id" in report.endpoints
    assert "Before:" not in markdown
    assert "After:" not in markdown
    assert "runtime-unverified" in markdown


def test_impact_report_maps_nested_next_component_to_its_route():
    diff = """diff --git a/apps/web/src/app/(main)/casino/[gameCode]/_components/dialog.tsx b/apps/web/src/app/(main)/casino/[gameCode]/_components/dialog.tsx
--- a/apps/web/src/app/(main)/casino/[gameCode]/_components/dialog.tsx
+++ b/apps/web/src/app/(main)/casino/[gameCode]/_components/dialog.tsx
@@ -1 +1 @@
-export const Dialog = Old
+export const Dialog = New
"""

    report = analyze_pr_impact(diff, head_sha="a" * 40)

    assert report.visual_routes == ["/casino/[gameCode]"]
    assert report.route_evidence["/casino/[gameCode]"] == "direct route component change"


def test_impact_report_maps_root_level_next_component_to_its_route():
    diff = """diff --git a/app/account/_components/form.tsx b/app/account/_components/form.tsx
--- a/app/account/_components/form.tsx
+++ b/app/account/_components/form.tsx
@@ -1 +1 @@
-export const Form = Old
+export const Form = New
"""

    report = analyze_pr_impact(diff, head_sha="a" * 40)

    assert report.visual_routes == ["/account"]


def test_impact_report_surfaces_outbound_integration_without_calling_it_a_public_api():
    diff = """diff --git a/apps/api/src/modules/marketing/adapters/ga4.ts b/apps/api/src/modules/marketing/adapters/ga4.ts
--- a/apps/api/src/modules/marketing/adapters/ga4.ts
+++ b/apps/api/src/modules/marketing/adapters/ga4.ts
@@ -1 +1,4 @@
-const endpoint = config.url
+const endpoint = 'https://www.google-analytics.com/mp/collect'
+fetch(endpoint, {
+  method: 'POST',
+})
"""

    report = analyze_pr_impact(diff, head_sha="a" * 40)
    markdown = report.to_markdown()

    assert report.endpoints == []
    assert report.external_integrations == ["POST https://www.google-analytics.com/mp/collect"]
    assert report.magnitude == ImpactMagnitude.MODERATE
    assert "External integration" in markdown
    assert "POST https://www.google-analytics.com/mp/collect" in markdown


def test_impact_report_maps_indirect_next_page_from_blast_radius():
    diff = """diff --git a/packages/ui/src/profile-card.tsx b/packages/ui/src/profile-card.tsx
--- a/packages/ui/src/profile-card.tsx
+++ b/packages/ui/src/profile-card.tsx
@@ -1 +1 @@
-export const ProfileCard = Old
+export const ProfileCard = New
"""

    report = analyze_pr_impact(
        diff,
        head_sha="d" * 40,
        blast_radius_paths=["apps/web/app/profile/page.tsx"],
    )

    assert report.visual_routes == ["/profile"]
    assert report.route_evidence["/profile"] == "inferred from indexed dependency"


def test_impact_report_builds_full_url_only_from_explicit_app_mapping():
    diff = """diff --git a/apps/web/app/settings/page.tsx b/apps/web/app/settings/page.tsx
--- a/apps/web/app/settings/page.tsx
+++ b/apps/web/app/settings/page.tsx
@@ -1 +1 @@
-return <Old />
+return <New />
"""

    report = analyze_pr_impact(
        diff,
        head_sha="f" * 40,
        web_base_urls={"apps/web": "https://app.example.com"},
    )

    assert report.route_urls["/settings"] == "https://app.example.com/settings"
    assert "https://app.example.com/settings" in report.to_markdown()


def test_impact_report_handles_backend_only_change():
    diff = """diff --git a/src/jobs/cleanup.py b/src/jobs/cleanup.py
--- a/src/jobs/cleanup.py
+++ b/src/jobs/cleanup.py
@@ -1 +1 @@
-timeout = 30
+timeout = 60
"""

    report = analyze_pr_impact(diff, head_sha="e" * 40)

    assert report.visual_routes == []
    assert report.background_processes == ["cleanup.py"]
    assert "Background processing" in report.to_markdown()


def test_impact_report_hides_empty_technical_evidence_for_test_only_diff():
    diff = """diff --git a/tests/test_worker.py b/tests/test_worker.py
--- a/tests/test_worker.py
+++ b/tests/test_worker.py
@@ -1 +1 @@
-def test_old(): pass
+def test_new(): pass
"""

    report = analyze_pr_impact(diff, head_sha="e" * 40)
    markdown = report.to_markdown()

    assert "Technical evidence" not in markdown
    assert "<details>" not in markdown


def test_compact_audit_report_groups_surfaces_and_hides_diff_noise():
    diff = """diff --git a/apps/web/src/app/(main)/casino/[gameCode]/_components/dialog.tsx b/apps/web/src/app/(main)/casino/[gameCode]/_components/dialog.tsx
--- a/apps/web/src/app/(main)/casino/[gameCode]/_components/dialog.tsx
+++ b/apps/web/src/app/(main)/casino/[gameCode]/_components/dialog.tsx
@@ -1 +1 @@
-import { Old } from './old'
+import { New } from './new'
diff --git a/apps/api/src/workers/measurement-delivery.worker.ts b/apps/api/src/workers/measurement-delivery.worker.ts
--- a/apps/api/src/workers/measurement-delivery.worker.ts
+++ b/apps/api/src/workers/measurement-delivery.worker.ts
@@ -1 +1,2 @@
-const endpoint = config.url
+const endpoint = 'https://www.google-analytics.com/mp/collect'
+fetch(endpoint, { method: 'POST' })
"""
    walkthrough = WalkthroughResult(
        summary=(
            "The casino return flow now reports unexpected failures, while the measurement "
            "worker sends events directly to GA4."
        ),
        file_changes=[
            WalkthroughFileEntry(
                path=("apps/web/src/app/(main)/casino/[gameCode]/_components/dialog.tsx"),
                change_type=FileChangeType.MODIFIED,
                description="Reports unexpected return-flow errors without swallowing them.",
            ),
            WalkthroughFileEntry(
                path="apps/api/src/workers/measurement-delivery.worker.ts",
                change_type=FileChangeType.MODIFIED,
                description="Sends queued measurements directly to GA4.",
            ),
        ],
        confidence_score=WalkthroughConfidenceScore(
            score=4,
            label="Safe with minor fixes",
            reason="The behavior is covered by focused tests.",
        ),
        sequence_diagram="sequenceDiagram\n    User->>Web: close dialog\n    Web-->>User: restore focus",
    )
    impact = analyze_pr_impact(diff, head_sha="b" * 40, walkthrough=walkthrough)

    markdown = build_compact_audit_report(
        head_sha="b" * 40,
        previous_sha="a" * 40,
        walkthrough=walkthrough,
        impact=impact,
        decisions=[
            ThreadDecision(
                thread_id="old",
                path="apps/api/src/ga4.ts",
                line=10,
                body="**Missing edge-case coverage**",
                fixed=True,
                status="fixed_by_code",
                evidence="Negative validator branches now have tests.",
            )
        ],
        new_findings=[],
        unverifiable_threads=[],
        reviewed_files=2,
        total_comments=0,
        existing_issues=0,
        provider_used="claude-cli",
        full_pr_revalidation=True,
        bot_name="mira",
        overlaps=[
            OverlapFinding(
                pr_number=42,
                url="https://github.com/acme/repo/pull/42",
                title="Related change",
                kind="merge_conflict",
                reason="Both PRs modify the same focus restoration flow.",
                confidence=0.9,
            )
        ],
    )

    assert markdown.startswith("<!-- mira-report:walkthrough:")
    assert "## Mira Audit · `bbbbbbbbbbbb`" in markdown
    assert "/casino/[gameCode]" in markdown
    assert "POST https://www.google-analytics.com/mp/collect" in markdown
    assert "change scope **moderate**" in markdown
    assert "<summary>Flow diagram</summary>" in markdown
    assert "```mermaid" in markdown
    assert "sequenceDiagram" in markdown
    assert "Potential overlap with other open PRs" in markdown
    assert "[#42](https://github.com/acme/repo/pull/42)" in markdown
    assert "**Fixed by code:** 1" in markdown
    assert "**New:**" not in markdown
    assert "Before:" not in markdown
    assert "After:" not in markdown
    assert "<details>" in markdown
    assert len(markdown.splitlines()) < 65


def test_compact_audit_report_omits_invalid_sequence_diagram():
    walkthrough = WalkthroughResult(
        summary="Changes.",
        sequence_diagram="this is not valid Mermaid",
    )
    impact = ImpactReport(
        head_sha="c" * 40,
        magnitude=ImpactMagnitude.MINIMAL,
    )

    markdown = build_compact_audit_report(
        head_sha="c" * 40,
        previous_sha="",
        walkthrough=walkthrough,
        impact=impact,
        decisions=[],
        new_findings=[],
        unverifiable_threads=[],
        reviewed_files=1,
        total_comments=0,
        existing_issues=0,
        provider_used="claude-cli",
        full_pr_revalidation=True,
        bot_name="mira",
    )

    assert "Flow diagram" not in markdown
    assert "```mermaid" not in markdown


def test_sqlite_store_migrates_legacy_review_events_with_empty_sha_lineage(tmp_path):
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE review_events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, pr_number INTEGER NOT NULL DEFAULT 0, "
        "pr_title TEXT NOT NULL DEFAULT '', pr_url TEXT NOT NULL DEFAULT '', "
        "author TEXT NOT NULL DEFAULT '', comments_posted INTEGER NOT NULL DEFAULT 0, "
        "blockers INTEGER NOT NULL DEFAULT 0, warnings INTEGER NOT NULL DEFAULT 0, "
        "suggestions INTEGER NOT NULL DEFAULT 0, files_reviewed INTEGER NOT NULL DEFAULT 0, "
        "lines_changed INTEGER NOT NULL DEFAULT 0, tokens_used INTEGER NOT NULL DEFAULT 0, "
        "duration_ms INTEGER NOT NULL DEFAULT 0, categories TEXT NOT NULL DEFAULT '', "
        "author_avatar_url TEXT NOT NULL DEFAULT '', reviewed_paths TEXT NOT NULL DEFAULT '', "
        "created_at REAL NOT NULL DEFAULT 0)"
    )
    connection.execute("INSERT INTO review_events (pr_number) VALUES (7)")
    connection.commit()
    connection.close()

    store = IndexStore(str(db_path))
    try:
        event = store.list_review_events_for_pr(7)[0]
        assert event.base_sha == ""
        assert event.head_sha == ""
        assert event.previous_head_sha == ""
    finally:
        store.close()
