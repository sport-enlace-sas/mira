"""Immutable per-SHA reports for review history, finding evolution, and impact."""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field

from mira.core.diff_parser import parse_diff
from mira.models import FileDiff, OverlapFinding, ReviewComment, ThreadDecision, WalkthroughResult

_REPORT_KIND_RE = re.compile(r"[^a-z0-9_-]+")
_SCOPE_RE = re.compile(r"[^a-z0-9._-]+")
_NEXT_PAGE_RE = re.compile(r"(?:^|/)app/(.+)/page\.(?:[jt]sx?)$")
_NEXT_ROOT_PAGE_RE = re.compile(r"(?:^|/)app/page\.(?:[jt]sx?)$")
_PAGES_PAGE_RE = re.compile(r"(?:^|/)pages/(.+)\.(?:[jt]sx?)$")


def make_report_marker(kind: str, scope: str) -> str:
    """Return an exact marker that can only match one report kind and SHA."""
    safe_kind = _REPORT_KIND_RE.sub("-", kind.strip().lower()).strip("-") or "report"
    safe_scope = _SCOPE_RE.sub("-", scope.strip().lower()).strip("-") or "unknown"
    return f"<!-- mira-report:{safe_kind}:{safe_scope} -->"


def _single_line(value: str, limit: int = 180) -> str:
    text = " ".join((value or "").split())
    text = text.replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _thread_title(body: str) -> str:
    for line in (body or "").splitlines():
        value = line.strip()
        if not value or value.startswith(("⚠️", "🐛", "💡", "🔒", "⚡", "🛑")):
            continue
        if value.startswith("**") and value.endswith("**"):
            value = value.strip("* ")
        return _single_line(value)
    return "Historical finding"


_STATUS_LABELS = {
    "new": "New",
    "regressed": "Regressed",
    "fixed_by_code": "Fixed by code",
    "still_present": "Still present",
    "rejected_false_positive": "Rejected false positive",
    "outdated_or_moved": "Outdated or moved",
    "resolved_before_audit": "Resolved before this audit",
    "unverifiable": "Unverifiable",
}
_STATUS_ICONS = {
    "new": "🆕",
    "regressed": "🔴",
    "fixed_by_code": "✅",
    "still_present": "⚠️",
    "rejected_false_positive": "🚫",
    "outdated_or_moved": "↪️",
    "resolved_before_audit": "◻️",
    "unverifiable": "❓",
}


def _finding_key(path: str, title: str) -> tuple[str, str]:
    normalized_title = " ".join(re.findall(r"[a-z0-9]+", title.lower()))
    return path.replace("\\", "/").lower(), normalized_title


def _group_evolution(
    *,
    decisions: list[ThreadDecision],
    new_findings: list[ReviewComment],
    unverifiable_threads: list[ThreadDecision],
    historical_resolved_threads: list[ThreadDecision] | None = None,
) -> dict[str, list[tuple[str, int, str, str]]]:
    grouped: dict[str, list[tuple[str, int, str, str]]] = {status: [] for status in _STATUS_LABELS}
    historical_by_key = {
        _finding_key(decision.path, _thread_title(decision.body)): decision
        for decision in (historical_resolved_threads or [])
    }
    for finding in new_findings:
        key = _finding_key(finding.path, finding.title)
        previous = historical_by_key.pop(key, None)
        status = "regressed" if previous else "new"
        evidence = (
            f"Matches previously resolved thread {previous.thread_id}."
            if previous
            else "current audit"
        )
        grouped[status].append((finding.path, finding.line, finding.title, evidence))
    for decision in [*decisions, *unverifiable_threads]:
        status = decision.status or ("fixed_by_code" if decision.fixed else "still_present")
        if status not in grouped:
            status = "unverifiable"
        grouped[status].append(
            (decision.path, decision.line, _thread_title(decision.body), decision.evidence)
        )
    for decision in historical_by_key.values():
        grouped["resolved_before_audit"].append(
            (decision.path, decision.line, _thread_title(decision.body), decision.evidence)
        )
    return grouped


def _render_evolution_section(
    *,
    head_sha: str,
    previous_sha: str,
    grouped: dict[str, list[tuple[str, int, str, str]]],
) -> list[str]:
    parts = ["### Finding evolution", ""]
    if previous_sha:
        parts.append(f"Scope: `{previous_sha[:12]}` → `{head_sha[:12]}`")
    else:
        parts.append(f"Scope: `{head_sha[:12]}` · **First recorded audit**")
    parts.append("")

    non_empty = [(status, entries) for status, entries in grouped.items() if entries]
    if not non_empty:
        parts.append("No finding changed state in this audit.")
        return parts

    for status, entries in non_empty:
        parts.append(f"- {_STATUS_ICONS[status]} **{_STATUS_LABELS[status]}:** {len(entries)}")

    detailed: list[tuple[str, tuple[str, int, str, str]]] = []
    for status, entries in non_empty:
        detailed.extend((status, entry) for entry in entries)
    if detailed:
        parts.extend(["", "<details>", "<summary>Finding evidence</summary>", ""])
        for status, (path, line, title, evidence) in detailed[:5]:
            location = f"{path}:{line}" if line > 0 else path
            suffix = f" — {_single_line(evidence)}" if evidence else ""
            parts.append(
                f"- {_STATUS_ICONS[status]} `{_code_span(location)}` — "
                f"{_single_line(title)}{suffix}"
            )
        if len(detailed) > 5:
            parts.append(f"- _…and {len(detailed) - 5} more findings_")
        parts.extend(["", "</details>"])
    return parts


def build_evolution_report(
    *,
    head_sha: str,
    previous_sha: str,
    decisions: list[ThreadDecision],
    new_findings: list[ReviewComment],
    unverifiable_threads: list[ThreadDecision],
    historical_resolved_threads: list[ThreadDecision] | None = None,
) -> str:
    """Render a compact comparison without mutating older reports."""
    grouped = _group_evolution(
        decisions=decisions,
        new_findings=new_findings,
        unverifiable_threads=unverifiable_threads,
        historical_resolved_threads=historical_resolved_threads,
    )
    parts = [make_report_marker("evolution", head_sha), "## Mira Finding Evolution", ""]
    parts.extend(
        _render_evolution_section(
            head_sha=head_sha,
            previous_sha=previous_sha,
            grouped=grouped,
        )[2:]
    )

    parts.extend(
        [
            "",
            "> Ambiguous or unverifiable findings are never presented as code fixes.",
        ]
    )
    return "\n".join(parts)


class ImpactMagnitude(enum.Enum):
    MINIMAL = "minimal"
    MODERATE = "moderate"
    MAJOR = "major"


@dataclass(frozen=True)
class LogicChange:
    path: str
    before: str = ""
    after: str = ""
    description: str = ""


@dataclass
class ImpactReport:
    head_sha: str
    magnitude: ImpactMagnitude
    changed_paths: list[str] = field(default_factory=list)
    visual_routes: list[str] = field(default_factory=list)
    route_evidence: dict[str, str] = field(default_factory=dict)
    route_urls: dict[str, str] = field(default_factory=dict)
    endpoints: list[str] = field(default_factory=list)
    external_integrations: list[str] = field(default_factory=list)
    background_processes: list[str] = field(default_factory=list)
    data_config_paths: list[str] = field(default_factory=list)
    logic_changes: list[LogicChange] = field(default_factory=list)

    def surface_rows(self) -> list[tuple[str, str]]:
        """Return a small, deduplicated functional surface summary."""
        rows: list[tuple[str, str]] = []
        if self.visual_routes:
            routes: list[str] = []
            for route in self.visual_routes[:3]:
                if route in self.route_urls:
                    routes.append(f"[{_link_text(route)}](<{self.route_urls[route]}>)")
                else:
                    routes.append(f"`{_code_span(route)}`")
            if len(self.visual_routes) > 3:
                routes.append(f"+{len(self.visual_routes) - 3} more")
            rows.append(("User-facing routes", ", ".join(routes)))
        if self.endpoints:
            shown = [f"`{_code_span(value)}`" for value in self.endpoints[:3]]
            if len(self.endpoints) > 3:
                shown.append(f"+{len(self.endpoints) - 3} more")
            rows.append(("Public API", ", ".join(shown)))
        if self.external_integrations:
            shown = [f"`{_code_span(value)}`" for value in self.external_integrations[:3]]
            if len(self.external_integrations) > 3:
                shown.append(f"+{len(self.external_integrations) - 3} more")
            rows.append(("External integration", ", ".join(shown)))
        if self.background_processes:
            shown = [f"`{_code_span(value)}`" for value in self.background_processes[:3]]
            if len(self.background_processes) > 3:
                shown.append(f"+{len(self.background_processes) - 3} more")
            rows.append(("Background processing", ", ".join(shown)))
        if self.data_config_paths:
            count = len(self.data_config_paths)
            rows.append(
                ("Data / configuration", f"{count} relevant file{'s' if count != 1 else ''}")
            )
        return rows

    def technical_evidence_lines(self, limit: int = 8) -> list[str]:
        changes = [change for change in self.logic_changes if not _is_low_signal_path(change.path)]
        if not changes:
            return []
        parts = ["<details>", f"<summary>Technical evidence ({len(changes)} files)</summary>", ""]
        for change in changes[:limit]:
            line = f"- `{_code_span(change.path)}`"
            if change.description:
                line += f" — {_single_line(change.description, 220)}"
            parts.append(line)
        if len(changes) > limit:
            parts.append(f"- _…and {len(changes) - limit} more files_")
        parts.extend(["", "</details>"])
        return parts

    def to_markdown(self) -> str:
        parts = [
            make_report_marker("impact", self.head_sha),
            "## Mira Change Impact",
            "",
            f"Scope: `{self.head_sha[:12]}` · magnitude: **{self.magnitude.value}**",
            "",
            "> Static analysis only · **runtime-unverified**. Routes are affected surfaces, "
            "not proof that a deployed page rendered successfully.",
            "",
            "### Affected surfaces",
            "",
        ]
        rows = self.surface_rows()
        if rows:
            parts.extend(["| Surface | Impact |", "|---|---|"])
            parts.extend(f"| {surface} | {impact} |" for surface, impact in rows)
        else:
            parts.append(
                "No user-facing route, public API, or external integration was identified."
            )
        technical_evidence = self.technical_evidence_lines()
        if technical_evidence:
            parts.extend(["", *technical_evidence])
        return "\n".join(parts)


def _code_span(value: str, limit: int = 180) -> str:
    text = " ".join((value or "").split()).replace("`", "'").replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _link_text(value: str) -> str:
    return _single_line(value).replace("[", "\\[").replace("]", "\\]")


def _route_from_path(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    if _NEXT_ROOT_PAGE_RE.search(normalized):
        return "/"
    match = _NEXT_PAGE_RE.search(normalized)
    if match:
        segments = []
        for segment in match.group(1).split("/"):
            if (segment.startswith("(") and segment.endswith(")")) or segment.startswith("@"):
                continue
            segment = re.sub(r"^\((?:\.{1,3})\)", "", segment)
            if segment:
                segments.append(segment)
        return "/" + "/".join(segments)
    match = _PAGES_PAGE_RE.search(normalized)
    if match:
        page = match.group(1)
        if page.startswith(("api/", "_app", "_document", "_error")):
            return None
        if page.endswith("/index"):
            page = page[: -len("/index")]
        elif page == "index":
            page = ""
        return "/" + page

    # A changed private route component belongs to the nearest App Router
    # ancestor. For example app/(main)/casino/[gameCode]/_components/dialog.tsx
    # affects /casino/[gameCode], while app/_contexts remains cross-cutting and
    # is intentionally not invented as a route.
    app_marker = "/app/"
    if normalized.startswith("app/"):
        relative = normalized[len("app/") :]
    elif app_marker in normalized:
        relative = normalized.split(app_marker, 1)[1]
    else:
        return None
    directories = relative.split("/")[:-1]
    route_segments: list[str] = []
    found_private_boundary = False
    for segment in directories:
        if (segment.startswith("(") and segment.endswith(")")) or segment.startswith("@"):
            continue
        if segment.startswith("_"):
            found_private_boundary = True
            break
        route_segments.append(segment)
    if found_private_boundary and route_segments:
        return "/" + "/".join(route_segments)
    return None


def _full_route_url(path: str, route: str, web_base_urls: dict[str, str]) -> str | None:
    normalized = path.replace("\\", "/").strip("/")
    roots = sorted(web_base_urls, key=len, reverse=True)
    for root in roots:
        clean_root = root.replace("\\", "/").strip("/")
        if normalized == clean_root or normalized.startswith(clean_root + "/"):
            base = web_base_urls[root].rstrip("/")
            return base + (route if route != "/" else "/")
    return None


def _hunk_text(file_diff: FileDiff) -> str:
    return "\n".join(hunk.content for hunk in file_diff.hunks)


def _changed_lines(file_diff: FileDiff) -> tuple[list[str], list[str]]:
    removed: list[str] = []
    added: list[str] = []
    for hunk in file_diff.hunks:
        for line in hunk.content.splitlines():
            if line.startswith("---") or line.startswith("+++"):
                continue
            if line.startswith("-"):
                value = line[1:].strip()
                if value:
                    removed.append(value)
            elif line.startswith("+"):
                value = line[1:].strip()
                if value:
                    added.append(value)
    return removed, added


def _join_route(base: str, child: str) -> str:
    pieces = [piece.strip("/") for piece in (base, child) if piece.strip("/")]
    return "/" + "/".join(pieces) if pieces else "/"


def _extract_endpoints(path: str, text: str) -> set[str]:
    endpoints: set[str] = set()
    controller_match = re.search(r"@Controller\(\s*['\"]([^'\"]*)['\"]\s*\)", text)
    controller = controller_match.group(1) if controller_match else ""
    for match in re.finditer(
        r"@(Get|Post|Put|Patch|Delete|Options|Head)\(\s*(?:['\"]([^'\"]*)['\"])?\s*\)",
        text,
    ):
        endpoints.add(f"{match.group(1).upper()} {_join_route(controller, match.group(2) or '')}")
    for match in re.finditer(
        r"@(?:router|app)\.(get|post|put|patch|delete|options|head)\(\s*['\"]([^'\"]+)['\"]",
        text,
        re.IGNORECASE,
    ):
        endpoints.add(f"{match.group(1).upper()} {match.group(2)}")
    for match in re.finditer(
        r"\b(?:app|router)\.(get|post|put|patch|delete|options|head)\(\s*['\"]([^'\"]+)['\"]",
        text,
        re.IGNORECASE,
    ):
        endpoints.add(f"{match.group(1).upper()} {match.group(2)}")
    for match in re.finditer(
        r"\b([A-Za-z_$][\w$]*)\s*:\s*(?:public|protected|private|admin|authed)Procedure\b",
        text,
    ):
        endpoints.add(f"tRPC {path}#{match.group(1)}")
    return endpoints


def _extract_external_integrations(path: str, text: str) -> set[str]:
    if _is_low_signal_path(path):
        return set()
    urls = {
        match.rstrip(".,;)")
        for match in re.findall(r"https?://[^\s'\"`]+", text)
        if not match.startswith(("http://localhost", "https://localhost"))
    }
    if not urls:
        return set()
    method_match = re.search(
        r"\bmethod\s*:\s*['\"](GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)['\"]",
        text,
        re.IGNORECASE,
    )
    method = method_match.group(1).upper() if method_match else "HTTP"
    return {f"{method} {url}" for url in urls}


def _is_low_signal_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    filename = normalized.rsplit("/", 1)[-1]
    return (
        "/tests/" in f"/{normalized}"
        or "/test/" in f"/{normalized}"
        or filename.endswith((".spec.ts", ".spec.tsx", ".test.ts", ".test.tsx", ".spec.py"))
        or normalized.startswith("docs/")
        or "/docs/" in normalized
        or normalized.endswith((".md", ".snap"))
        or "/migrations/meta/" in normalized
    )


def _is_background_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    return bool(
        re.search(r"/(?:workers?|jobs?|cron|queues?)/", normalized)
        or re.search(r"(?:worker|job|consumer)\.[^.]+$", normalized)
    )


def _is_data_or_config_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    return any(
        token in normalized
        for token in (
            "/migrations/",
            "/schema/",
            ".schema.",
            "/config/",
            ".env",
            "/pulumi/",
            "infrastructure/",
        )
    )


def _classify_magnitude(
    changed_paths: list[str],
    routes: set[str],
    endpoints: set[str],
    external_integrations: set[str],
    background_processes: set[str],
    data_config_paths: set[str],
    lines: int,
) -> ImpactMagnitude:
    lower_paths = "\n".join(changed_paths).lower()
    critical_terms = (
        "migration",
        "/schema",
        "auth",
        "wallet",
        "payment",
        "kyc",
        "permission",
        "security",
    )
    shared_terms = ("packages/", "shared/", "design-system", "contracts/")
    if (
        any(term in lower_paths for term in critical_terms)
        or len(routes) >= 3
        or len(changed_paths) > 20
        or lines > 800
    ):
        return ImpactMagnitude.MAJOR
    if (
        routes
        or endpoints
        or external_integrations
        or background_processes
        or data_config_paths
        or any(term in lower_paths for term in shared_terms)
        or len(changed_paths) > 5
        or lines > 200
    ):
        return ImpactMagnitude.MODERATE
    return ImpactMagnitude.MINIMAL


def analyze_pr_impact(
    diff_text: str,
    *,
    head_sha: str,
    walkthrough: WalkthroughResult | None = None,
    blast_radius_paths: list[str] | None = None,
    web_base_urls: dict[str, str] | None = None,
) -> ImpactReport:
    """Build a deterministic impact report from the PR diff and indexed dependents."""
    patch = parse_diff(diff_text)
    descriptions = {
        entry.path: entry.description for entry in (walkthrough.file_changes if walkthrough else [])
    }
    changed_paths = [file.path for file in patch.files]
    route_evidence: dict[str, str] = {}
    route_urls: dict[str, str] = {}
    endpoints: set[str] = set()
    external_integrations: set[str] = set()
    background_processes: set[str] = set()
    data_config_paths: set[str] = set()
    logic_changes: list[LogicChange] = []
    total_lines = 0

    for file in patch.files:
        total_lines += file.total_changes
        route = _route_from_path(file.path)
        if route:
            is_page = bool(
                _NEXT_ROOT_PAGE_RE.search(file.path)
                or _NEXT_PAGE_RE.search(file.path)
                or _PAGES_PAGE_RE.search(file.path)
            )
            route_evidence[route] = (
                "direct page change" if is_page else "direct route component change"
            )
            full_url = _full_route_url(file.path, route, web_base_urls or {})
            if full_url:
                route_urls[route] = full_url
        text = _hunk_text(file)
        endpoints.update(_extract_endpoints(file.path, text))
        external_integrations.update(_extract_external_integrations(file.path, text))
        if _is_background_path(file.path) and not _is_low_signal_path(file.path):
            background_processes.add(file.path.rsplit("/", 1)[-1])
        if _is_data_or_config_path(file.path) and not _is_low_signal_path(file.path):
            data_config_paths.add(file.path)
        removed, added = _changed_lines(file)
        logic_changes.append(
            LogicChange(
                path=file.path,
                before=removed[0] if removed else "",
                after=added[0] if added else "",
                description=descriptions.get(file.path, ""),
            )
        )

    for path in blast_radius_paths or []:
        route = _route_from_path(path)
        if route and route not in route_evidence:
            route_evidence[route] = "inferred from indexed dependency"
            full_url = _full_route_url(path, route, web_base_urls or {})
            if full_url:
                route_urls[route] = full_url

    routes = set(route_evidence)
    magnitude = _classify_magnitude(
        changed_paths,
        routes,
        endpoints,
        external_integrations,
        background_processes,
        data_config_paths,
        total_lines,
    )
    return ImpactReport(
        head_sha=head_sha,
        magnitude=magnitude,
        changed_paths=changed_paths,
        visual_routes=sorted(routes),
        route_evidence=route_evidence,
        route_urls=route_urls,
        endpoints=sorted(endpoints),
        external_integrations=sorted(external_integrations),
        background_processes=sorted(background_processes),
        data_config_paths=sorted(data_config_paths),
        logic_changes=logic_changes,
    )


def build_compact_audit_report(
    *,
    head_sha: str,
    previous_sha: str,
    walkthrough: WalkthroughResult,
    impact: ImpactReport,
    decisions: list[ThreadDecision],
    new_findings: list[ReviewComment],
    unverifiable_threads: list[ThreadDecision],
    reviewed_files: int,
    total_comments: int,
    existing_issues: int,
    provider_used: str,
    full_pr_revalidation: bool,
    bot_name: str,
    fallback_used: bool = False,
    historical_resolved_threads: list[ThreadDecision] | None = None,
    skipped_paths: list[str] | None = None,
    total_paths: list[str] | None = None,
    index_was_empty: bool = False,
    overlaps: list[OverlapFinding] | None = None,
) -> str:
    """Render one high-signal, immutable audit comment for a reviewed SHA."""
    parts = [
        make_report_marker("walkthrough", head_sha),
        f"## Mira Audit · `{head_sha[:12]}`",
        "",
        _single_line(walkthrough.summary, 700),
    ]

    if walkthrough.confidence_score:
        confidence = walkthrough.confidence_score
        verdict = confidence.label or "Reviewed"
        parts.extend(["", f"**Verdict:** {verdict} · confidence {confidence.score}/5"])
    context = [f"change scope **{impact.magnitude.value}**"]
    if full_pr_revalidation:
        context.append("full PR revalidation")
    if provider_used:
        provider = provider_used + (" (fallback)" if fallback_used else "")
        context.append(f"provider `{_code_span(provider)}`")
    if context:
        parts.append(" · ".join(context))

    if walkthrough.sequence_diagram:
        from mira.llm.mermaid import harden_mermaid

        diagram = harden_mermaid(walkthrough.sequence_diagram)
        if diagram:
            parts.extend(
                [
                    "",
                    "<details>",
                    "<summary>Flow diagram</summary>",
                    "",
                    "```mermaid",
                    diagram,
                    "```",
                    "",
                    "</details>",
                ]
            )

    if overlaps:
        parts.extend(["", "> ⚠️ **Potential overlap with other open PRs:**"])
        for overlap in overlaps[:3]:
            link = (
                f"[#{overlap.pr_number}]({overlap.url})" if overlap.url else f"#{overlap.pr_number}"
            )
            parts.append(f"> - {link} · {_single_line(overlap.reason)}")
        if len(overlaps) > 3:
            parts.append(f"> - _…and {len(overlaps) - 3} more_")

    parts.extend(["", "### Affected surfaces", ""])
    surface_rows = impact.surface_rows()
    if surface_rows:
        parts.extend(["| Surface | Impact |", "|---|---|"])
        parts.extend(f"| {surface} | {value} |" for surface, value in surface_rows)
    else:
        parts.append("No user-facing route, public API, or external integration was identified.")

    grouped = _group_evolution(
        decisions=decisions,
        new_findings=new_findings,
        unverifiable_threads=unverifiable_threads,
        historical_resolved_threads=historical_resolved_threads,
    )
    parts.extend(
        [
            "",
            *_render_evolution_section(
                head_sha=head_sha,
                previous_sha=previous_sha,
                grouped=grouped,
            ),
        ]
    )

    coverage = [f"{reviewed_files} file{'s' if reviewed_files != 1 else ''} reviewed"]
    coverage.append(f"{total_comments} new finding{'s' if total_comments != 1 else ''}")
    if existing_issues:
        coverage.append(f"{existing_issues} prior unresolved")
    parts.extend(["", "### Coverage", "", " · ".join(coverage)])
    if skipped_paths:
        total = len(total_paths) if total_paths else reviewed_files + len(skipped_paths)
        parts.append(
            f"> {len(skipped_paths)} of {total} files were skipped. "
            f"Use `@{bot_name} review-rest` for the remainder."
        )
    if index_was_empty:
        parts.append(
            "> Repository dependency index was unavailable; cross-file impact may be incomplete."
        )
    if impact.visual_routes:
        parts.append(
            "> Routes are inferred statically and remain **runtime-unverified** until visual QA."
        )

    technical_evidence = impact.technical_evidence_lines()
    if technical_evidence:
        parts.extend(["", *technical_evidence])
    parts.extend(["", f"> Comment `@{bot_name} help` for available commands."])
    return "\n".join(parts)
